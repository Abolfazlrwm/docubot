"""Shared settings for every environment. Environment-specific values live in dev.py / prod.py."""

import os
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY", "dev-only-insecure-secret-key-with-sufficient-length-for-hmac"
)

ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "drf_spectacular",
    # Local
    "apps.users",
    "apps.documents",
    "apps.chat",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "docubot"),
        "USER": os.environ.get("POSTGRES_USER", "docubot"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "docubot"),
        "HOST": os.environ.get("POSTGRES_HOST", "db"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.environ.get("REDIS_URL", "redis://redis:6379/0"),
    }
}

AUTH_USER_MODEL = "users.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Maximum accepted upload size in bytes (10 MB). Enforced in apps.documents.validators.
MAX_UPLOAD_SIZE = int(os.environ.get("MAX_UPLOAD_SIZE", 10 * 1024 * 1024))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "DocuChat API",
    "DESCRIPTION": "RAG knowledge-base API: users upload documents and chat over them.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "TAGS": [
        {"name": "auth", "description": "Registration, login and the current user."},
        {"name": "documents", "description": "Upload, list, inspect and delete documents."},
    ],
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

# Celery: broker and results on Redis; late acks so a crashed worker loses no tasks.
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", REDIS_URL)
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 270
CELERY_TASK_ALWAYS_EAGER = _env_bool("CELERY_TASK_ALWAYS_EAGER", default=False)

# A document claimed for longer than this is considered abandoned by a dead
# worker and may be re-claimed (defaults to the Celery time limit plus slack).
PROCESSING_STALE_AFTER_SECONDS = int(
    os.environ.get(
        "PROCESSING_STALE_AFTER_SECONDS",
        int(os.environ.get("CELERY_TASK_TIME_LIMIT", "300")) + 60,
    )
)

# Vector dimensions of the configured embedding model.
EMBEDDING_DIMENSIONS = int(os.environ.get("EMBEDDING_DIMENSIONS", "384"))

# Text chunking for embedding.
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "1000"))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "200"))

# Retrieval / search.
RETRIEVAL_TOP_K = int(os.environ.get("RETRIEVAL_TOP_K", "5"))
RETRIEVAL_MAX_TOP_K = int(os.environ.get("RETRIEVAL_MAX_TOP_K", "20"))
# Cosine-similarity cutoff (1 - cosine distance); calibrated with the real model.
RETRIEVAL_MIN_SCORE = float(os.environ.get("RETRIEVAL_MIN_SCORE", "0.3"))
QUERY_MAX_LENGTH = int(os.environ.get("QUERY_MAX_LENGTH", "2000"))

# pgvector HNSW tuning. On pgvector >= 0.8.0 iterative scans are enabled inside
# the retrieval transaction so owner filters can still return enough rows;
# hnsw.ef_search below is only used on older pgvector versions.
HNSW_ITERATIVE_SCAN = os.environ.get("HNSW_ITERATIVE_SCAN", "relaxed_order")
HNSW_MAX_SCAN_TUPLES = int(os.environ.get("HNSW_MAX_SCAN_TUPLES", "20000"))
HNSW_EF_SEARCH = int(os.environ.get("HNSW_EF_SEARCH", "40"))

# LLM: "fake" (default, offline), "openai" (any OpenAI-compatible base URL),
# "anthropic". Model names come from env only — nothing is hardcoded.
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "fake")
# Empty base URL selects the provider's canonical default endpoint.
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT_SECONDS", "60"))
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "1024"))

# RAG: how many previous conversation messages to include as history.
CHAT_HISTORY_MESSAGES = int(os.environ.get("CHAT_HISTORY_MESSAGES", "6"))

# Streaming answers (Server-Sent Events): how often to flush the Fake provider's
# output and the idle timeout for the OpenAI-compatible stream reader.
STREAM_CHUNK_SIZE = int(os.environ.get("STREAM_CHUNK_SIZE", "24"))

# Embeddings: "local" (fastembed, default), "fake" (tests/dev), "openai" (HTTP).
EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "local")
EMBEDDING_MODEL = os.environ.get(
    "EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)
EMBEDDING_BATCH_SIZE = int(os.environ.get("EMBEDDING_BATCH_SIZE", "32"))
EMBEDDING_CACHE_DIR = os.environ.get("EMBEDDING_CACHE_DIR", "/home/appuser/.cache/fastembed")
EMBEDDING_TIMEOUT_SECONDS = float(os.environ.get("EMBEDDING_TIMEOUT_SECONDS", "30"))
# Only used when EMBEDDING_PROVIDER=openai.
EMBEDDING_API_BASE = os.environ.get("EMBEDDING_API_BASE", "")
EMBEDDING_API_KEY = os.environ.get("EMBEDDING_API_KEY", "")
EMBEDDING_API_MODEL = os.environ.get("EMBEDDING_API_MODEL", "")
