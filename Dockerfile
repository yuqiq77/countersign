# One image, two entrypoints: api (default) and worker (v4 §7).
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations

RUN pip install --no-cache-dir .
RUN python -m playwright install --with-deps chromium \
    && browser_path="$(python -c 'from playwright.sync_api import sync_playwright; p = sync_playwright().start(); print(p.chromium.executable_path); p.stop()')" \
    && ln -sf "$browser_path" /usr/local/bin/countersign-chromium

EXPOSE 8000

CMD ["uvicorn", "countersign.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
