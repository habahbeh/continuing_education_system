"""الأرشيف التاريخي والأرصدة الافتتاحية — app shell. Models are built in Sprint 8."""

from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class DatamigrationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.datamigration"
    label = "datamigration"
    verbose_name = _("الأرشيف التاريخي والأرصدة الافتتاحية")


# ---------------------------------------------------------------------------
# ARCHITECTURAL BOUNDARY — Q-02 / ADR-013 / D-26
#
# This app must NEVER import apps.billing, apps.cashbox or apps.settlements.
#
# Historical Excel data is a READ-ONLY ARCHIVE, structurally isolated from the
# production ledger. Migrating old payments as production receipts would
# fabricate documents this system never issued. The only gateway from archive
# to ledger is an individually reviewed and approved OpeningBalance (BR-094).
#
# Enforced by the static test A-04 in tests/test_architecture.py, which fails
# the build on violation.
# ---------------------------------------------------------------------------
