"""
Base settings — University of Petra Continuing Education Center Management System.

Binding decisions enforced here:
- ADR-004  MySQL 8.4 LTS, InnoDB, utf8mb4 / utf8mb4_0900_ai_ci, STRICT_ALL_TABLES
- ADR-005  Decimal only for money (DECIMAL(12,3)); float is forbidden
- ADR-008  Services layer; transactions are explicit in services (ATOMIC_REQUESTS = False)
- ADR-009  Effective-dated settings; no hardcoded business constants
- ADR-003  Arabic RTL-first UI; no CDN, everything served locally
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent

load_dotenv(BASE_DIR / ".env")


def env(key: str, default: str | None = None) -> str:
    value = os.environ.get(key, default)
    if value is None:
        raise RuntimeError(f"Required environment variable is missing: {key}")
    return value


def env_bool(key: str, default: bool = False) -> bool:
    return os.environ.get(key, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_int(key: str, default: int) -> int:
    return int(os.environ.get(key, default))


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY", "insecure-placeholder-override-in-env")
DEBUG = False
ALLOWED_HOSTS: list[str] = []

# ADR-004 / DATA_MODEL §11.2 — BigAutoField everywhere.
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# people.User is the custom user model (Sprint 1, before the first migration).
AUTH_USER_MODEL = "people.User"

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

# Order mirrors the dependency direction documented in DATA_MODEL §2.
LOCAL_APPS = [
    "apps.core",
    "apps.people",
    "apps.catalog",
    "apps.partners",
    "apps.operations",
    "apps.billing",
    "apps.cashbox",
    "apps.settlements",
    "apps.expenses",
    "apps.reporting",
    "apps.datamigration",
]

INSTALLED_APPS = DJANGO_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Captures actor + IP for the audit trail (ADR-010).
    "apps.core.middleware.AuditContextMiddleware",
    # Q-12: 30 minutes of INACTIVITY ends the session. Must run after
    # AuthenticationMiddleware (it needs request.user) and after
    # AuditContextMiddleware (the expiry event is audited with its IP).
    "apps.people.middleware.IdleSessionMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                # The sidebar, built from the permission matrix (Sprint 8B).
                # A context processor rather than a per-view context key so
                # that no view can forget it and render a page with no way out.
                "apps.people.nav.navigation",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database — MySQL 8.4 LTS (ADR-004)
# ---------------------------------------------------------------------------
# STRICT_ALL_TABLES is non-negotiable: without it MySQL truncates values
# silently instead of raising, which is unacceptable in a financial system.
# core.checks.check_mysql_sql_mode turns a violation into a boot failure.
MYSQL_INIT_COMMAND = (
    "SET sql_mode='STRICT_ALL_TABLES,ERROR_FOR_DIVISION_BY_ZERO,"
    "NO_ZERO_DATE,NO_ZERO_IN_DATE,NO_ENGINE_SUBSTITUTION,ONLY_FULL_GROUP_BY'"
)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": env("DB_NAME", "continuing_education"),
        "USER": env("DB_USER", "ce_app"),
        "PASSWORD": env("DB_PASSWORD", ""),
        "HOST": env("DB_HOST", "127.0.0.1"),
        "PORT": env("DB_PORT", "3308"),
        "CONN_MAX_AGE": 60,
        # ADR-008: transactions are opened explicitly inside services,
        # never implicitly per request.
        "ATOMIC_REQUESTS": False,
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": MYSQL_INIT_COMMAND,
            "isolation_level": "read committed",
        },
        "TEST": {
            "CHARSET": "utf8mb4",
            "COLLATION": "utf8mb4_0900_ai_ci",
        },
    }
}

# ---------------------------------------------------------------------------
# Authentication — Q-12: LOCAL accounts in v1
# ---------------------------------------------------------------------------
# Exactly ONE backend, deliberately. The decision is not "SSO is bad" but "do
# not build a dependency on the university's directory before it exists".
# Adding SAML/LDAP later means appending a backend here; no business rule reads
# an external identity, so nothing else has to change. T-278 fails the build if
# a directory backend appears in v1.
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]

LOGIN_URL = "/auth/login/"
# Sprint 8I-1 — the dashboard, not the health check. `core.views.home`
# and the login view both read this rather than naming a route.
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/auth/login/"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Internationalisation — Arabic RTL first (ADR-003)
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "ar"
LANGUAGES = [("ar", "العربية")]
LOCALE_PATHS = [BASE_DIR / "locale"]
USE_I18N = True
USE_L10N = True

# All timestamps are stored in UTC and displayed in Amman time
# (DATA_MODEL §11.1/§11.2).
TIME_ZONE = "Asia/Amman"
USE_TZ = True

# ---------------------------------------------------------------------------
# Static files — no CDN, everything local (ADR-003)
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Attachments live outside the web root and are served through a permission
# checked view, never by a direct link (OPEN_QUESTIONS Q-13).
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# ---------------------------------------------------------------------------
# Money — Q-04 (DECIMAL(12,3) stored, 2 decimals displayed)
# ---------------------------------------------------------------------------
# These two mirror apps.core.fields and exist so that a reader of the settings
# file sees the decision. The authoritative definition is in apps/core/fields.py;
# the display precision is an EffectiveSetting (`money_display_dp`), because it
# is a business decision and may change (ADR-009).
MONEY_MAX_DIGITS = 12
MONEY_DECIMAL_PLACES = 3

# ---------------------------------------------------------------------------
# Sessions & security baseline
# ---------------------------------------------------------------------------
# A hard backstop only. The BUSINESS rule — 30 minutes of inactivity — lives in
# apps.people.middleware.IdleSessionMiddleware and reads
# `session_idle_timeout_minutes` from EffectiveSetting, because a threshold in
# a settings file is a business constant in disguise (BR-086, Q-12).
SESSION_COOKIE_AGE = 30 * 60
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
CSRF_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "apps": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
