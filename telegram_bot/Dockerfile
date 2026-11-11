FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY fmbot_tg ./fmbot_tg
RUN pip install --no-cache-dir .
ENV DATABASE_URL=sqlite+aiosqlite:////data/fmbot.db
VOLUME /data
CMD ["python", "-m", "fmbot_tg"]
