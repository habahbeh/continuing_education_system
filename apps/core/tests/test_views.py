"""Health page test."""

from __future__ import annotations

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_health_page_renders_and_reports_database(client) -> None:  # type: ignore[no-untyped-def]
    response = client.get(reverse("health"))
    assert response.status_code == 200

    content = response.content.decode("utf-8")
    assert 'dir="rtl"' in content
    assert 'lang="ar"' in content
    assert "النظام يعمل" in content
    assert "متصلة" in content
    assert "MySQL 8.4" in content
