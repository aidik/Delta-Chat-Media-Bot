import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from deltabot_cli import BotCli
from deltachat2 import MsgData, events
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

RESPOND_TO_RAW = os.getenv("RESPOND_TO", "")
if not RESPOND_TO_RAW:
    logger.warning("RESPOND_TO is empty - bot will not respond to anyone")
    RESPOND_TO = []
else:
    RESPOND_TO = [a for a in RESPOND_TO_RAW.replace(" ", "").split(",") if a]

DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/downloads")
AUDIO_DIR = os.getenv("AUDIO_DIR", "/downloads-audio")
MAX_HEIGHT = os.getenv("MAX_HEIGHT", "").strip()
SUBTITLE_LANGS = os.getenv("SUBTITLE_LANGS", "en").strip()
JOB_TIMEOUT = int(os.getenv("JOB_TIMEOUT", "14400"))

VIDEO_ARCHIVE = os.path.join(DOWNLOAD_DIR, ".yt-dlp-archive.txt")
AUDIO_ARCHIVE = os.path.join(AUDIO_DIR, ".yt-dlp-archive.txt")

URL_REGEX = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

cli = BotCli("mediabot")


@dataclass
class Job:
    accid: int
    chat_id: int
    url: str
    mode: str  # 'video' or 'audio'
    index: int
    total: int


job_queue: "queue.Queue[tuple[object, Job]]" = queue.Queue()


def extract_urls(text: str) -> list[str]:
    if not text:
        return []
    found = URL_REGEX.findall(text)
    seen = set()
    out = []
    for raw in found:
        # Strip common trailing punctuation that's unlikely to be part of the URL.
        url = raw.rstrip(".,;:!?)]}>")
        try:
            parsed = urlparse(url)
        except ValueError:
            continue
        if parsed.scheme not in ("http", "https"):
            continue
        if not parsed.netloc:
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024.0 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TiB"


def send(bot, accid: int, chat_id: int, text: str) -> None:
    try:
        bot.rpc.send_msg(accid, chat_id, MsgData(text=text))
    except Exception as e:
        logger.error(f"Failed to send message: {e}")


def build_video_args(url: str) -> list[str]:
    if MAX_HEIGHT:
        fmt = (
            f"bv*[height<={MAX_HEIGHT}]+ba/b[height<={MAX_HEIGHT}]/bv*+ba/b"
        )
    else:
        fmt = "bv*+ba/b"

    args = [
        "yt-dlp",
        "--no-playlist",
        "--no-overwrites",
        "--restrict-filenames",
        "--download-archive", VIDEO_ARCHIVE,
        "-f", fmt,
        "--merge-output-format", "mp4",
        "--embed-metadata",
        "--embed-thumbnail",
        "-o", os.path.join(DOWNLOAD_DIR, "%(uploader)s - %(title).150B [%(id)s].%(ext)s"),
        "--trim-filenames", "200",
        "--print", "after_move:filepath",
        "--newline",
    ]
    if SUBTITLE_LANGS:
        args += [
            "--embed-subs",
            "--sub-langs", SUBTITLE_LANGS,
            "--convert-subs", "srt",
        ]
    args += ["--", url]
    return args


def build_audio_args(url: str) -> list[str]:
    return [
        "yt-dlp",
        "--no-playlist",
        "--no-overwrites",
        "--restrict-filenames",
        "--download-archive", AUDIO_ARCHIVE,
        "-f", "ba/b",
        "--extract-audio",
        "--audio-format", "m4a",
        "--audio-quality", "0",
        "--embed-metadata",
        "--embed-thumbnail",
        "-o", os.path.join(AUDIO_DIR, "%(uploader)s - %(title).150B [%(id)s].%(ext)s"),
        "--trim-filenames", "200",
        "--print", "after_move:filepath",
        "--newline",
        "--", url,
    ]


def parse_after_move_filepath(stdout: str) -> Optional[str]:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line and os.path.isabs(line) and os.path.exists(line):
            return line
    return None


def truncate_stderr(stderr: str, limit: int = 1500) -> str:
    lines = [ln for ln in stderr.strip().splitlines() if ln.strip()]
    tail = "\n".join(lines[-10:])
    if len(tail) > limit:
        tail = tail[-limit:]
    return tail or "(no error output)"


def run_job(bot, job: Job) -> None:
    send(
        bot,
        job.accid,
        job.chat_id,
        f"downloading ({job.index}/{job.total}) [{job.mode}]: {job.url}",
    )

    if job.mode == "audio":
        args = build_audio_args(job.url)
    else:
        args = build_video_args(job.url)

    logger.info(f"Running yt-dlp: {args}")

    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=JOB_TIMEOUT,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired:
        send(
            bot,
            job.accid,
            job.chat_id,
            f"error: job timed out after {JOB_TIMEOUT}s: {job.url}",
        )
        return
    except FileNotFoundError:
        send(
            bot,
            job.accid,
            job.chat_id,
            "error: yt-dlp binary not found in container",
        )
        return

    if proc.returncode != 0:
        msg = truncate_stderr(proc.stderr or proc.stdout or "")
        send(
            bot,
            job.accid,
            job.chat_id,
            f"error downloading {job.url}:\n{msg}",
        )
        return

    filepath = parse_after_move_filepath(proc.stdout or "")

    if not filepath:
        # rc=0 with no after_move line → archive said "already downloaded"
        send(
            bot,
            job.accid,
            job.chat_id,
            f"already downloaded: {job.url}",
        )
        return

    try:
        size = os.path.getsize(filepath)
        size_str = human_size(size)
    except OSError:
        size_str = "?"

    send(
        bot,
        job.accid,
        job.chat_id,
        f"done: {os.path.basename(filepath)} ({size_str})",
    )


def worker_loop() -> None:
    while True:
        bot, job = job_queue.get()
        try:
            run_job(bot, job)
        except Exception as e:
            logger.error(f"Worker error on {job.url}: {e}", exc_info=True)
            send(
                bot,
                job.accid,
                job.chat_id,
                f"error: unexpected failure on {job.url}: {e}",
            )
        finally:
            job_queue.task_done()


HELP_TEXT = (
    "Send me one or more URLs and I'll download the video to your media library.\n"
    "Prefix the message with /audio to extract audio only.\n"
    "Examples:\n"
    "  https://www.youtube.com/watch?v=...\n"
    "  /audio https://www.youtube.com/watch?v=..."
)


@cli.on(events.NewMessage)
def process_message(bot, accid, event):
    msg = event.msg
    sender = msg.sender.name_and_addr
    logger.info(f"Received message from {sender} in chat {msg.chat_id}")

    if msg.sender.address not in RESPOND_TO:
        send(
            bot,
            accid,
            msg.chat_id,
            f"Sorry {sender}, I'm not allowed to talk to you.",
        )
        return

    text = (msg.text or "").strip()
    mode = "video"
    if text.lower().startswith("/audio"):
        mode = "audio"
        text = text[len("/audio"):].strip()

    urls = extract_urls(text)
    if not urls:
        send(bot, accid, msg.chat_id, HELP_TEXT)
        return

    total = len(urls)
    for i, url in enumerate(urls, start=1):
        job = Job(
            accid=accid,
            chat_id=msg.chat_id,
            url=url,
            mode=mode,
            index=i,
            total=total,
        )
        job_queue.put((bot, job))
        send(
            bot,
            accid,
            msg.chat_id,
            f"queued ({i}/{total}) [{mode}]: {url}",
        )


def startup_checks() -> None:
    if not shutil.which("yt-dlp"):
        logger.error("yt-dlp binary not found in PATH")
        sys.exit(1)
    if not shutil.which("ffmpeg"):
        logger.warning("ffmpeg not found in PATH - muxing/audio extraction will fail")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(AUDIO_DIR, exist_ok=True)


if __name__ == "__main__":
    if "serve" in sys.argv:
        startup_checks()
        logger.info(f"Starting bot. Responding to: {RESPOND_TO}")
        logger.info(f"Video dir: {DOWNLOAD_DIR}  Audio dir: {AUDIO_DIR}")
        logger.info(f"MAX_HEIGHT={MAX_HEIGHT or '(unset, true best)'}  SUBTITLE_LANGS={SUBTITLE_LANGS}")
        threading.Thread(target=worker_loop, daemon=True).start()
    cli.start()
