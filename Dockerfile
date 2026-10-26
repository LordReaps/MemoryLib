FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN python -c "from pathlib import Path; path = Path('requirements.txt'); path.write_text(path.read_text(encoding='utf-16'), encoding='utf-8')" \
    && pip install --no-cache-dir -r requirements.txt \
    && addgroup --system app \
    && adduser --system --ingroup app app \
    && mkdir -p /app/media \
    && chown app:app /app/media

COPY --chown=app:app . .

USER app

EXPOSE 8000
