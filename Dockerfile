FROM python:3.13-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl unzip \
 && curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh \
 && deno --version \
 && apt-get purge -y --auto-remove curl unzip \
 && rm -rf /var/lib/apt/lists/*

RUN useradd -u 1000 -m bot

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY deltabot.py deltabot.py
COPY bot-avatar.jpg bot-avatar.jpg

RUN mkdir -p /downloads /downloads-audio /dcconfig \
 && chown -R bot:bot /app /downloads /downloads-audio /dcconfig

USER bot

VOLUME ["/downloads", "/downloads-audio", "/dcconfig"]

CMD ["sh", "-c", "\
  if [ ! -f /dcconfig/.initialized ]; then \
    python deltabot.py init --config-dir /dcconfig 'DCACCOUNT:https://nine.testrun.org/new' && \
    python deltabot.py config --config-dir /dcconfig displayname 'Delta Media Bot' && \
    python deltabot.py config --config-dir /dcconfig selfstatus 'Send me a video URL and I will save it to your media library.' && \
    python deltabot.py config --config-dir /dcconfig selfavatar './bot-avatar.jpg' && \
    touch /dcconfig/.initialized; \
  fi && \
  python deltabot.py link --config-dir /dcconfig && \
  python deltabot.py serve --config-dir /dcconfig"]
