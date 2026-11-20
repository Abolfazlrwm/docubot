FROM python:3.14-slim

# Production settings are the default: the image refuses to boot without a
# DJANGO_SECRET_KEY and starts with DEBUG=False. Local development passes
# DJANGO_SETTINGS_MODULE=config.settings.dev explicitly.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN adduser --disabled-password --gecos "" appuser \
    && mkdir -p /home/appuser/.cache/fastembed /app/staticfiles \
    && chown -R appuser:appuser /home/appuser/.cache \
    && chown -R appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/', timeout=4).status == 200 else 1)"

# runserver is a dev tool; production traffic is served by gunicorn.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "60"]
