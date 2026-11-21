FROM python:3.12-slim
WORKDIR /app
# Fonts for chart and receipt images: Noto CJK covers Latin, Cyrillic, Greek and CJK names
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY fmbot_tg ./fmbot_tg
RUN pip install --no-cache-dir .
ENV DATABASE_URL=sqlite+aiosqlite:////data/fmbot.db
VOLUME /data
CMD ["python", "-m", "fmbot_tg"]
