"""DocuChat project: expose the Celery app so `celery -A config` works."""

from .celery import app as celery_app

__all__ = ("celery_app",)
