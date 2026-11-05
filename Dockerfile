FROM python:3.12.10-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends chromium libgeos-c1v5 libspatialindex6 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.7.8
WORKDIR /app
COPY deploy/web-extract/pyproject.toml deploy/web-extract/uv.lock ./
RUN uv sync --frozen --no-dev
COPY deploy/web-extract/app.py ./
COPY src/harness ./harness
ENV PYTHONPATH=/app CHROME_PATH=/usr/bin/chromium PORT=8080
EXPOSE 8080
CMD ["/app/.venv/bin/uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
