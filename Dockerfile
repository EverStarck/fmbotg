FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.7.19 /uv /usr/local/bin/uv
WORKDIR /app

# Fonts for chart and receipt images: Noto CJK covers Latin, Cyrillic, Greek and CJK names
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# Dependencies first, from the lock file, so code changes don't reinstall them
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project
COPY README.md ./
COPY fmbotg ./fmbotg
RUN uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH" DATABASE_URL=sqlite+aiosqlite:////data/fmbot.db
VOLUME /data
CMD ["python", "-m", "fmbotg"]
