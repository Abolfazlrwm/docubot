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
    "django.contrib.postgres",  # FTS search vectors; required by Django 6's postgres checks
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "drf_spectacular",
    # Local
    "apps.users",
    "apps.documents",
    "apps.chat",
    "apps.apikeys",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # X-RateLimit-* headers on API-key responses (see apps.apikeys.middleware).
    "apps.apikeys.middleware.APIKeyRateLimitHeadersMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
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
# Project-level static assets (the /demo/ page) live in the root static/ dir.
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Maximum accepted upload size in bytes (10 MB). Enforced in apps.documents.validators.
MAX_UPLOAD_SIZE = int(os.environ.get("MAX_UPLOAD_SIZE", 10 * 1024 * 1024))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        # JWT first (unchanged challenge behaviour); the API-key authenticator
        # only engages when the request carries an X-API-Key header.
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "apps.apikeys.authentication.APIKeyAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        # Enforces API-key scopes; a no-op for every other authentication method.
        "apps.apikeys.permissions.HasAPIKeyScope",
        "rest_framework.permissions.IsAuthenticated",
    ),
    # Per-key rate limiting: engages only for X-API-Key traffic. Views with
    # explicit throttle_classes list it alongside their scoped throttle.
    "DEFAULT_THROTTLE_CLASSES": ("apps.apikeys.throttling.APIKeyRateThrottle",),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Scoped rate limits (see apps/common/throttling.py). Views opt in with a
    # throttle_scope; rates are env-tunable for load tests and big instances.
    "DEFAULT_THROTTLE_RATES": {
        "auth": os.environ.get("THROTTLE_AUTH", "30/min"),
        "chat": os.environ.get("THROTTLE_CHAT", "60/min"),
        "search": os.environ.get("THROTTLE_SEARCH", "60/min"),
        "upload": os.environ.get("THROTTLE_UPLOAD", "60/hour"),
    },
}

# Default per-minute budget for personal API keys (each key can override it
# with its own rate_limit; JWT traffic is never metered by it).
APIKEY_RATE_LIMIT = int(os.environ.get("APIKEY_RATE_LIMIT", "120"))

SPECTACULAR_SETTINGS = {
    "TITLE": "DocuBot API",
    "DESCRIPTION": "RAG knowledge-base API: upload documents, then search and chat over them.",
    "VERSION": "1.4.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "TAGS": [
        {"name": "auth", "description": "Registration, login and the current user."},
        {"name": "documents", "description": "Upload, list, inspect and delete documents."},
        {"name": "search", "description": "Semantic search over ready documents."},
        {"name": "chat", "description": "Conversations and RAG answers (JSON and SSE streaming)."},
        {"name": "keys", "description": "Personal API keys for programmatic access."},
        {"name": "stats", "description": "Admin-only usage analytics."},
        {"name": "health", "description": "Service health for load balancers and orchestrators."},
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

# Retrieval signals: "vector" (embeddings only), "keyword" (Postgres FTS only)
# or "hybrid" (both, fused with Reciprocal Rank Fusion).
#   SEARCH_MODE    -> default for the explicit /api/v1/search/ endpoint
#   RETRIEVAL_MODE -> default for RAG answers, where recall matters most
SEARCH_MODE = os.environ.get("SEARCH_MODE", "vector")
RETRIEVAL_MODE = os.environ.get("RETRIEVAL_MODE", "hybrid")
# Postgres text-search configuration. "simple" lowercases and splits but does
# not stem, which behaves uniformly across languages (English and Persian alike).
FTS_CONFIG = os.environ.get("FTS_CONFIG", "simple")
# Hybrid retrieval fetches this many candidates per signal before fusing.
HYBRID_CANDIDATE_MULTIPLIER = int(os.environ.get("HYBRID_CANDIDATE_MULTIPLIER", "3"))
# RRF constant: dampens the advantage of the very top ranks (standard 60).
HYBRID_RRF_K = int(os.environ.get("HYBRID_RRF_K", "60"))

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

# Rewrite a follow-up question into a standalone search query before retrieval
# ("and the second one?" -> "second quarter revenue 2025"). Best effort: any
# LLM failure falls back to the raw question, so answers never block on it.
QUERY_REWRITE_ENABLED = _env_bool("QUERY_REWRITE_ENABLED", default=True)
REWRITE_MAX_CHARS = int(os.environ.get("REWRITE_MAX_CHARS", "400"))
# How many recent messages the rewriter may look at to resolve pronouns.
QUERY_REWRITE_MAX_HISTORY = int(os.environ.get("QUERY_REWRITE_MAX_HISTORY", "6"))

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
