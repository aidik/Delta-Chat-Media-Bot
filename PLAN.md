# Delta Media Bot — Transformation Plan

Convert the existing **Delta Chat AI Bot** into a **Delta Chat → yt-dlp** bot that downloads
videos (or audio) from URLs sent by whitelisted family members and stores them on a
host-mounted path consumable by Plex / Jellyfin.

---

## 1. Behavior summary

| Trigger                          | Action                                                  | Output dir          |
| -------------------------------- | ------------------------------------------------------- | ------------------- |
| Plain message with one+ URL(s)   | Download highest-quality video, mux to MP4              | `/downloads`        |
| `/audio <url> [<url> …]`         | Download bestaudio, save as M4A (or original codec)     | `/downloads-audio`  |
| Anything else                    | Short help reply                                        | —                   |

- One job at a time (single worker, FIFO queue). Multiple URLs in one message → queued sequentially.
- Per job, the bot sends three Delta Chat messages: **queued → downloading → done/error**.
- Duplicates (same video ID, recorded in a yt-dlp archive file) are skipped with `"already downloaded: <filename>"`.
- Sender whitelist (`RESPOND_TO`) is preserved unchanged from the existing bot.

---

## 2. Configuration (`.env`)

Drop all `AI_*` vars. New / kept vars:

```ini
# Whitelist (kept from current bot) — REQUIRED
RESPOND_TO=user1@example.com,user2@example.com

# Output paths inside the container (host-mount these)
DOWNLOAD_DIR=/downloads
AUDIO_DIR=/downloads-audio

# Quality
# Empty = true best (default). Set e.g. 1080 to cap height.
MAX_HEIGHT=

# Subtitles to embed as soft tracks (comma-separated). Empty = no subs.
SUBTITLE_LANGS=en,cs

# yt-dlp / ffmpeg job timeout in seconds (safety net, not a duration cap)
JOB_TIMEOUT=14400
```

No domain allow-list, no cookies, no disk-space guard, no size/duration cap (per family-trust assumption).

---

## 3. File layout on the host

Flat, both for video and audio. Filename template:

```
%(uploader)s - %(title)s [%(id)s].%(ext)s
```

with `--restrict-filenames` so yt-dlp guarantees ASCII-only, no shell metachars, no spaces in problematic places. Plex/Jellyfin "Other Videos" / "Music Videos" / "Home Videos" libraries handle this fine. Thousands of files in one directory is no problem on ext4/xfs/btrfs — only matters if someone `ls`'s it.

A hidden archive file lives alongside each library:

- `/downloads/.yt-dlp-archive.txt`
- `/downloads-audio/.yt-dlp-archive.txt`

This is yt-dlp's native dedupe mechanism. Each line is `<extractor> <id>`.

---

## 4. yt-dlp invocation

### Video (default)

```bash
yt-dlp \
  --no-playlist \
  --no-overwrites \
  --restrict-filenames \
  --download-archive /downloads/.yt-dlp-archive.txt \
  -f "bv*[height<=${MAX_HEIGHT}]+ba/b[height<=${MAX_HEIGHT}]/bv*+ba/b"  # MAX_HEIGHT-aware
  --merge-output-format mp4 \
  --embed-metadata --embed-thumbnail \
  --embed-subs --sub-langs "$SUBTITLE_LANGS" \
  --convert-subs srt \
  -o "/downloads/%(uploader)s - %(title)s [%(id)s].%(ext)s" \
  --print after_move:filepath \
  --newline \
  -- <URL>
```

Format-string detail: when `MAX_HEIGHT` is empty, the script substitutes the simple `-f "bv*+ba/b"` instead of the height-bounded one. Subs are embedded as **soft tracks only** — no `--embed-subs` burn-in (that's a separate flag, `--embed-subs` does not burn in by default; we never pass `--write-auto-subs` either, so auto-generated subs are excluded as you requested).

### Audio (`/audio` prefix)

```bash
yt-dlp \
  --no-playlist \
  --no-overwrites \
  --restrict-filenames \
  --download-archive /downloads-audio/.yt-dlp-archive.txt \
  -f "ba/b" \
  --extract-audio --audio-format m4a --audio-quality 0 \
  --embed-metadata --embed-thumbnail \
  -o "/downloads-audio/%(uploader)s - %(title)s [%(id)s].%(ext)s" \
  --print after_move:filepath \
  --newline \
  -- <URL>
```

`--audio-format m4a` keeps AAC streams as-is when source is AAC (no re-encode); falls back to encoding only when needed. Plex/Jellyfin music libraries read m4a + embedded metadata + embedded cover natively.

### Trailing `--`

The `--` separator before `<URL>` tells yt-dlp to stop parsing flags, so a hostile-looking URL can never be misread as an option. Combined with `subprocess.run(args_list, shell=False)`, the full command-injection surface is gone.

---

## 5. Code structure (`deltabot.py` rewrite)

Single file, ~250 lines. Modules used:

- `subprocess` — yt-dlp invocation, list-args only, `shell=False`.
- `threading` + `queue.Queue` — single worker thread, FIFO.
- `urllib.parse` + `re` — URL extraction & validation.
- `shutil` — for resolving the yt-dlp binary path at startup.
- existing `deltabot_cli` / `deltachat2` — unchanged.

### Components

1. **`extract_urls(text) -> list[str]`**
   - Regex finds candidate `https?://…` substrings.
   - Each is parsed with `urllib.parse.urlparse`; kept only if scheme ∈ {http, https} and `netloc` is non-empty.
   - Returns de-duplicated, order-preserved list.

2. **`Job` dataclass** — `{accid, chat_id, url, mode: 'video'|'audio'}`.

3. **`worker()` thread**
   - Pulls jobs off a global `queue.Queue`.
   - Sends "downloading…" message.
   - Runs yt-dlp via `subprocess.run([...], capture_output=True, text=True, timeout=JOB_TIMEOUT, shell=False)`.
   - Parses `--print after_move:filepath` from stdout to learn the final filename.
   - Detects "already in archive" condition (yt-dlp returns rc=0 with no `after_move` line) → sends "already downloaded" message.
   - On rc=0 with new file: sends `"done: <basename> (<human size>)"`.
   - On rc≠0 or `TimeoutExpired`: sends `"error: <last ~10 lines of stderr>"` (truncated to <1500 chars to stay polite in DC).

4. **`process_message` handler**
   - Whitelist check (existing logic).
   - Strips `/audio` prefix → mode flag.
   - Calls `extract_urls`. If empty → short help reply listing usage and current mode.
   - For each URL: enqueue job, reply `"queued (<n>/<m>): <url>"`.

5. **Startup**
   - Resolve `yt-dlp` binary with `shutil.which`; fail loudly if missing.
   - Ensure `DOWNLOAD_DIR` and `AUDIO_DIR` exist (`os.makedirs(..., exist_ok=True)`).
   - Start worker thread (daemon=True).
   - Then existing `cli.start()`.

---

## 6. Anti-injection guarantees (recap)

| Vector                                    | Mitigation                                          |
| ----------------------------------------- | --------------------------------------------------- |
| Shell metachars in URL (`&&`, `;`, `\``)  | `subprocess.run([...], shell=False)` — never a shell |
| URL-shaped flag (`--exec`, `-o`)          | Trailing `--` in argv stops flag parsing            |
| Filename injection from video metadata    | `--restrict-filenames` (ASCII, safe charset)        |
| Path traversal via `%(title)s`            | Same — `--restrict-filenames` strips `/` and `..`   |
| Hostile URL scheme (`file://`, `data://`) | `urlparse` scheme check (http/https only)           |
| Container escape via writable host paths  | Bot runs as non-root user; only `/downloads*` and `/dcconfig` are writable bind mounts |

There is no `os.system`, no `shell=True`, no f-string interpolation into any shell command anywhere in the new code.

---

## 7. Dockerfile changes

```dockerfile
FROM python:3.13-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN useradd -u 1000 -m bot
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY deltabot.py bot-avatar.jpg ./
RUN mkdir -p /downloads /downloads-audio /dcconfig \
 && chown -R bot:bot /app /downloads /downloads-audio /dcconfig

USER bot
VOLUME ["/downloads", "/downloads-audio", "/dcconfig"]

CMD ["sh", "-c", "\
  if [ ! -f /dcconfig/.initialized ]; then \
    python deltabot.py --config-dir /dcconfig init 'DCACCOUNT:https://nine.testrun.org/new' && \
    python deltabot.py --config-dir /dcconfig config displayname 'Delta Media Bot' && \
    python deltabot.py --config-dir /dcconfig config selfstatus 'Send me a video URL and I will save it to your media library.' && \
    python deltabot.py --config-dir /dcconfig config selfavatar './bot-avatar.jpg' && \
    touch /dcconfig/.initialized; \
  fi && \
  python deltabot.py --config-dir /dcconfig link && \
  python deltabot.py --config-dir /dcconfig serve"]
```

Key changes vs current Dockerfile:
- `slim` base (smaller image).
- `ffmpeg` installed via apt (needed for muxing & audio extraction).
- `yt-dlp` installed via pip (newer than Debian package; pinned in `requirements.txt`).
- Non-root user `bot` (uid 1000) so files on the host bind-mount have predictable ownership.
- Display name / status updated.

### Host bind-mount example (docker-compose snippet, for the README)

```yaml
volumes:
  - /mnt/media/youtube:/downloads
  - /mnt/media/music:/downloads-audio
  - ./dcconfig:/dcconfig
```

---

## 8. `requirements.txt`

```text
deltabot-cli
python-dotenv
yt-dlp
```

(Drop `requests`, drop bare `dotenv`, add pinned `yt-dlp`. Pins to be added at implementation time so we capture latest stable.)

---

## 9. README rewrite

Replace AI-bot wording with media-bot wording. Sections:
1. What it does.
2. Quick start (Docker + bind mounts).
3. `.env` reference.
4. Usage examples (paste URL → video; `/audio <url>` → audio).
5. Plex/Jellyfin library setup tip ("Other Videos" agent, scan periodically).
6. Security notes.

---

## 10. File-by-file delta

| File                | Action                                                          |
| ------------------- | --------------------------------------------------------------- |
| `deltabot.py`       | Rewrite (keep imports, whitelist, `events.NewMessage` skeleton) |
| `Dockerfile`        | Rewrite per §7                                                  |
| `requirements.txt`  | Replace contents per §8                                         |
| `.env.example`      | Rewrite per §2                                                  |
| `README.md`         | Rewrite per §9                                                  |
| `bot-avatar.jpg`    | Keep (or replace later if you want a new avatar)                |
| `LICENSE`           | Keep                                                            |
| `.gitignore`        | Keep                                                            |
| `PLAN.md`           | This file (delete after implementation if you want)             |

No new files beyond `PLAN.md`.

---

## 11. Implementation order

1. Rewrite `deltabot.py` (worker queue, URL extraction, yt-dlp invocation, reply messages).
2. Update `requirements.txt` and `Dockerfile`.
3. Update `.env.example`.
4. Rewrite `README.md`.
5. Local smoke test: build image, point at a test Delta Chat account, send a YouTube URL, verify file lands in mounted dir and Plex picks it up.
6. Send `/audio <url>`, verify m4a in audio dir.
7. Send a malicious-looking URL (`https://example.com/foo && rm -rf /tmp/x`) — verify it's either passed to yt-dlp as one argv element and rejected by yt-dlp, or fails URL validation. Either way, no shell side-effects.
8. Send the same URL twice — verify "already downloaded" path.

---

## 12. Out of scope (explicit)

- Domain allow-listing.
- Cookie/login support.
- Disk-space guard.
- Per-job size/duration caps.
- Live progress percentage updates.
- Parallel downloads.
- Playlist support (`--no-playlist` is hard-coded; can be revisited later).
- Web UI / admin interface.
- Auto-cleanup / retention policy.

These can be added later without disturbing the core design.
