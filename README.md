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
| `YT_EXTRACTOR_ARGS` | No     | `youtube:player_client=default,tv,web_safari` | Passed to `yt-dlp --extractor-args`. Try alternate player clients to dodge YouTube "Sign in to confirm you're not a bot" errors. Empty = yt-dlp defaults. |
| `BGUTIL_POT_PROVIDER_URL` | No | *(unset)*       | URL of a [bgutil PO Token provider](#optional-po-token-provider-for-youtube) sidecar (e.g. `http://bgutil-provider:4416`). Empty = plugin idle. |
| `COOKIES_FILE`    | No       | *(unset)*            | In-container path to a Netscape-format cookies file. Needed for YouTube `LOGIN_REQUIRED` content. See [Optional: Cookies](#optional-cookies-for-login-required-content). |

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

## YouTube bot-detection notes

YouTube increasingly responds to unauthenticated requests with `Sign in to confirm
you're not a bot`. The bot defaults to `--extractor-args
youtube:player_client=default,tv,web_safari`, which tries alternate player clients
that often slip past the challenge. When YouTube clamps down on the current set,
tune `YT_EXTRACTOR_ARGS` — the [yt-dlp youtube extractor docs](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#youtube)
list current client names. If alternate clients stop working, the next-cheapest
mitigation is the [PO Token provider plugin](https://github.com/Brainicism/bgutil-ytdlp-pot-provider);
`--cookies` is the most reliable but requires maintaining a YouTube account.

Keeping `yt-dlp` itself fresh also matters — `requirements.txt` pins `yt-dlp[default]`
unpinned, so a periodic image rebuild picks up upstream extractor fixes. The bundled
[`.github/workflows/rebuild.yml`](.github/workflows/rebuild.yml) does this weekly when
configured with Docker Hub credentials.

## Optional: PO Token provider for YouTube

When `--extractor-args` tweaks stop working, the next-cheapest mitigation is running
the [`bgutil-ytdlp-pot-provider`](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)
sidecar. It mints YouTube **Proof-of-Origin** tokens that yt-dlp attaches to each
request, which is what YouTube's bot challenge actually wants. The bot's image already
ships the matching pip plugin — it stays inert until you point it at a provider.

### 1. Run the provider container

```bash
docker run -d --name bgutil-provider --init --restart unless-stopped \
  brainicism/bgutil-ytdlp-pot-provider
```

The provider listens on port `4416` inside the container. If you publish it to the
host with `-p 4416:4416`, any yt-dlp on the same host can use it.

### 2. Make the bot reach it

The bot container must be able to resolve and connect to the provider:

- **Same Docker network** (recommended). Create a user-defined bridge once
  (`docker network create media-bot`) and start both containers with
  `--network media-bot`. Inside, the bot reaches the provider at
  `http://bgutil-provider:4416`.
- **Host networking / published port.** If the provider publishes `4416` to the
  host, point the bot at `http://<docker-host-ip>:4416` (not `127.0.0.1`, which
  resolves *inside* the bot container).

### 3. Tell the bot to use it

Set in `.env`:

```
BGUTIL_POT_PROVIDER_URL=http://bgutil-provider:4416
```

…or pass `-e BGUTIL_POT_PROVIDER_URL=...` to `docker run`. On the next download the
bot will append `--extractor-args youtubepot-bgutilhttp:base_url=<your URL>` to
yt-dlp. Leaving the variable unset keeps the plugin dormant — it's a pure opt-in.

## Optional: Cookies for login-required content

PO Tokens solve YouTube's *bot challenge*. They do **not** solve `LOGIN_REQUIRED`
— a separate, increasingly common YouTube response (notably for Shorts,
age-restricted, region-locked, and embedded content) where YouTube simply demands
an authenticated session before returning any player data. yt-dlp surfaces both
errors with the misleading "Sign in to confirm you're not a bot" message; only
cookies fix the second one.

**Risk note.** A Google account whose cookies are exported to `yt-dlp` *can* get
flagged for unusual activity. At hobby volumes (a few downloads a day, with the
PO Token sidecar minimising chatter) it's typically fine for long stretches, but
expect occasional re-pairing. Don't use cookies from an account you can't afford
to have temporarily restricted.

### 1. Export cookies from a logged-in browser

Easiest path is the [*Get cookies.txt LOCALLY*](https://addons.mozilla.org/firefox/addon/cookies-txt/)
browser extension (Firefox or Chromium). Log into YouTube, open the extension on
`youtube.com`, click *Export As → cookies.txt* — that's a Netscape-format file
yt-dlp accepts directly.

### 2. Place it where the container can read it

```bash
mkdir -p /mnt/configs/yt-cookies
mv ~/Downloads/cookies.txt /mnt/configs/yt-cookies/yt-cookies.txt
sudo chown 1000:1000 /mnt/configs/yt-cookies/yt-cookies.txt
sudo chmod 600 /mnt/configs/yt-cookies/yt-cookies.txt
```

The file must be readable by uid `1000` (the in-container `bot` user). `chmod
600` is enough since uid matches; the cookies are sensitive — treat the file as
secret.

### 3. Bind-mount and point the bot at it

Add to `docker run`:

```
-v /mnt/configs/yt-cookies/yt-cookies.txt:/cookies/yt-cookies.txt:ro \
-e COOKIES_FILE=/cookies/yt-cookies.txt \
```

(`:ro` keeps yt-dlp from rewriting the file with refreshed cookies; this is
fine for the static-export workflow above. If you ever switch to a strategy
where you want yt-dlp to refresh the cookies, drop `:ro`.)

On TrueNAS Apps, do the equivalent: a host-path volume pointing at the cookies
file mounted at `/cookies/yt-cookies.txt`, plus `COOKIES_FILE` in the
environment.

### 4. Re-pairing

When YouTube invalidates the session (you'll see `LOGIN_REQUIRED` come back even
on previously-working URLs), repeat step 1 and overwrite the file. No bot
restart required — yt-dlp re-reads the file on every invocation.

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