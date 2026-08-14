"""Test settings — fast, deterministic, still MySQL 8.4."""

from __future__ import annotations

from .base import *

DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

# Fast hashing keeps the suite quick without changing behaviour under test.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

LOGGING["root"]["level"] = "WARNING"  # type: ignore[index]
