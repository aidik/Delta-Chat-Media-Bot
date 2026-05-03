# Delta Media Bot

A [Delta Chat](https://delta.chat/) bot that downloads videos (or audio) from URLs you send it,
using [yt-dlp](https://github.com/yt-dlp/yt-dlp), and saves them into a host-mounted directory
ready for Plex, Jellyfin, or any other media server.

## Features

- Send a URL → highest-quality video saved as MP4 with embedded metadata, thumbnail, and subtitles.
- Prefix a message with `/audio` → audio-only download saved as M4A.
- Multiple URLs in one message are queued and processed sequentially (one job at a time).
- Per-job replies: **queued → downloading → done / already downloaded / error**.
- Sender whitelist — only authorized Delta Chat addresses can use the bot.
- Built-in deduplication (yt-dlp download archive).

## Prerequisites

- Docker (recommended) — image bundles `yt-dlp[default]`, `ffmpeg`, and the [Deno](https://deno.com)
  JavaScript runtime needed for YouTube extraction (see [JS runtime](#js-runtime-deno) below).
- A host directory you want videos saved into, accessible to your media server.

## Quick Start (Docker)

### 1. Clone and configure

```bash
git clone https://github.com/aidik/Delta-Chat-Media-Bot.git
cd Delta-Chat-Media-Bot
cp .env.example .env
# edit .env — at minimum set RESPOND_TO
```

### 2a. Build and run from source

```bash
docker build -t delta-chat-media-bot .

docker run -d --name delta-chat-media-bot \
  --env-file .env \
  -v /mnt/media/youtube:/downloads \
  -v /mnt/media/music:/downloads-audio \
  -v ./dcconfig:/dcconfig \
  delta-chat-media-bot
```

Replace `/mnt/media/youtube` and `/mnt/media/music` with the host paths Plex / Jellyfin will scan.

The `dcconfig` bind mount is what makes the bot's Delta Chat account survive container restarts.
The host directory must be writable by uid `1000` (the in-container `bot` user). If you create it
fresh, run `mkdir -p ./dcconfig && sudo chown 1000:1000 ./dcconfig` before the first start.

### 2b. Run the pre-built Docker image

A pre-built image is available on
[Docker Hub](https://hub.docker.com/r/aidik/delta-chat-media-bot). No cloning or building required —
just pass your configuration as environment variables:

```bash
docker run -d --name delta-chat-media-bot \
  -v /mnt/media/youtube:/downloads \
  -v /mnt/media/music:/downloads-audio \
  -v ./dcconfig:/dcconfig \
  -e RESPOND_TO="user1@example.com,user2@example.com" \
  -e MAX_HEIGHT="1080" \
  -e SUBTITLE_LANGS="en,cs" \
  -e JOB_TIMEOUT="14400" \
  docker.io/aidik/delta-chat-media-bot:latest
```

At minimum only `RESPOND_TO` is required; everything else falls back to the defaults documented in
[Configuration](#configuration-env). The same `dcconfig` ownership note from 2a applies.

### 3. Pair the bot with your Delta Chat client

The bot prints an invite link to its log every time it starts:

```bash
docker logs delta-media-bot
```

Open the link in Delta Chat to add the bot as a contact. Then send it a YouTube
(or any yt-dlp-supported) URL.

## Configuration (`.env`)

| Variable          | Required | Default              | Description                                                            |
| ----------------- | -------- | -------------------- | ---------------------------------------------------------------------- |
| `RESPOND_TO`      | **Yes**  | —                    | Comma-separated list of allowed Delta Chat addresses                   |
| `DOWNLOAD_DIR`    | No       | `/downloads`         | Container path where videos are written                                |
| `AUDIO_DIR`       | No       | `/downloads-audio`   | Container path where audio extracts are written                        |
| `MAX_HEIGHT`      | No       | *(unset = true best)* | Max video height (e.g. `1080`, `2160`)                                |
| `SUBTITLE_LANGS`  | No       | `en`                 | Comma-separated subtitle languages to embed as soft tracks (no auto subs) |
| `JOB_TIMEOUT`     | No       | `14400`              | Per-job timeout in seconds                                             |

## Usage

| You send                              | Bot does                                   |
| ------------------------------------- | ------------------------------------------ |
| `https://www.youtube.com/watch?v=…`   | Downloads video to `DOWNLOAD_DIR`          |
| Several URLs in one message           | Queues all, processes sequentially         |
| `/audio https://…`                    | Downloads audio only to `AUDIO_DIR`        |
| Anything without a URL                | Replies with a short help message          |

Output filename template (flat directory):

```
<uploader> - <title> [<id>].<ext>
```

with `--restrict-filenames` so names are ASCII, no spaces or shell metachars.

## JS runtime (Deno)

As of late 2025 yt-dlp requires an external JavaScript runtime to solve YouTube's challenge scripts;
without one you'll see `WARNING: [youtube] No supported JavaScript runtime could be found` followed
by `ERROR: This video is not available`. The Dockerfile installs Deno (the recommended runtime)
into `/usr/local/bin/deno` so it's on the bot's `PATH`. The challenge-solver scripts themselves ship
with the `yt-dlp[default]` extra (pinned in `requirements.txt`).

If you run `deltabot.py` outside Docker, install Deno yourself
([instructions](https://docs.deno.com/runtime/getting_started/installation/)) and make sure it's on
`PATH`. See the [yt-dlp EJS wiki](https://github.com/yt-dlp/yt-dlp/wiki/EJS) for alternative
runtimes (Node, Bun, QuickJS).

## Plex / Jellyfin setup tip

Point a **"Other Videos"** (Plex) or **"Home Videos"** / **"Movies"** (Jellyfin) library at your
`DOWNLOAD_DIR` host path, and a **Music** library at `AUDIO_DIR`. Schedule periodic library
scans and the bot's output will show up automatically.

## Security notes

- The bot never invokes a shell. yt-dlp is run with list arguments and `shell=False`, so a URL
  containing `&&`, `;`, backticks, etc. is just an argv string yt-dlp will reject.
- Only `http://` and `https://` URLs pass validation.
- A trailing `--` separates flags from the URL in argv, so URL-shaped flags can't be reinterpreted.
- yt-dlp's `--restrict-filenames` ensures video metadata can't produce path-traversal or
  shell-unsafe filenames.
- Container runs as a non-root user (`uid 1000`).
- Only `RESPOND_TO`-listed Delta Chat addresses can trigger downloads.

## Running outside Docker

If you run `deltabot.py` directly (e.g. for development), pass `--config-dir <path>` **after** the
subcommand, not before:

```bash
# correct — config-dir reaches the subcommand parser
python deltabot.py init   --config-dir ./dcconfig "DCACCOUNT:https://nine.testrun.org/new"
python deltabot.py serve  --config-dir ./dcconfig

# silently broken — uses the default appdirs path (~/.config/mediabot)
python deltabot.py --config-dir ./dcconfig serve
```

`deltabot-cli` registers `--config-dir` on the main parser **and** on every subparser with the same
default. When the flag appears before the subcommand, the subparser's default value silently
overwrites the value you supplied, and the bot writes its account data to the appdirs default
instead of where you asked. The bundled Docker `CMD` already places the flag in the right spot.

## How it works

1. `deltabot-cli` listens for incoming Delta Chat messages.
2. Sender is checked against `RESPOND_TO`.
3. The text is scanned for URLs (with optional `/audio` prefix).
4. Each valid URL is enqueued. A single background worker thread processes jobs one at a time.
5. The worker invokes `yt-dlp` (with `ffmpeg` for muxing) and reports status back to the chat.