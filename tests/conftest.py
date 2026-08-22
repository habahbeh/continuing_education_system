"""
Fixtures for the cross-app readiness run (Sprint 8E).

Borrowed rather than rebuilt. The readiness flows exist to prove that the
apps' real machinery works in sequence, so they are driven by the same
fixtures the apps' own tests use — a local imitation could be subtly
different in exactly the way that hides a seam.

Selective imports, not a star. ``cashier`` comes across under its own name
because ``make_paid_enrollment`` requests it as a fixture — aliasing it would
leave that dependency unresolvable — while the readiness module builds its own
named staff for everything it drives directly.
"""

from __future__ import annotations

from apps.datamigration.tests.conftest import sample_workbook  # noqa: F401
from apps.settlements.tests.conftest import (  # noqa: F401
    cash_method,
    cashier,
    cohort_with_agreement,
    make_paid_enrollment,
    partner,
    percent_agreement,
    priced_catalog,
)
