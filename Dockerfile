FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DJANGO_SETTINGS_MODULE=config.settings PYTHONPATH=/app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=app:app . .
RUN chmod +x /app/docker/entrypoint.sh \
    && mkdir -p /app/staticfiles /app/run \
    && chown -R app:app /app/staticfiles /app/run
USER app
EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
