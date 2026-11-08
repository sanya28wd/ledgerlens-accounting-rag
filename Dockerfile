FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.22 /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
RUN .venv/bin/python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"
COPY app ./app
COPY web ./web
COPY samples ./samples
RUN useradd --create-home ledger && mkdir /var/data && chown ledger:ledger /var/data
ENV DATA_DIR=/var/data PYTHONUNBUFFERED=1
USER ledger
EXPOSE 8000
CMD ["sh", "-c", "exec .venv/bin/uvicorn app.main:create_application --factory --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
