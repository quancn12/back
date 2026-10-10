FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./

# Run as an unprivileged account. runtime/ is a named volume in the production
# compose file and inherits this ownership when Docker first creates it.
RUN useradd --create-home --uid 10001 vietsage \
    && mkdir -p /app/runtime/sources /app/runtime/logs \
    && chown -R vietsage:vietsage /app

USER vietsage

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
