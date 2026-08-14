"""Local development settings."""

from __future__ import annotations

from .base import *
from .base import env_bool

DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]

# Local dev over plain HTTP.
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
