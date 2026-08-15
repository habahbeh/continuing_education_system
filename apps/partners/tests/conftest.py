"""Fixtures for the agreement tests."""

from __future__ import annotations

import pytest


@pytest.fixture
def priced_catalog(active_semester, seeded_settings):
    """The Sprint 3 demo catalogue — agreements snapshot real programmes."""
    from django.core.management import call_command

    from apps.catalog.models import PriceList, PriceListStatus

    call_command("seed_catalog_demo", "--approve", verbosity=0)
    return PriceList.objects.get(status=PriceListStatus.APPROVED)
