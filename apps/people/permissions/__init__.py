"""
Permission engine (BR-080, PERMISSIONS.md).

Public surface:

* ``policy.require``  — enforce, audit the refusal, raise. Use this everywhere.
* ``policy.evaluate`` — the decision object, with the deciding rule attached.
* ``policy.is_allowed`` — boolean, for drawing UI. Never a control.
"""

from __future__ import annotations

from apps.people.permissions.policy import (
    Decision,
    evaluate,
    is_allowed,
    require,
    require_report,
)
from apps.people.permissions.separation import (
    SeparationOfDutiesMixin,
    assert_different_actor,
    assert_second_certifier_differs,
)

__all__ = [
    "Decision",
    "SeparationOfDutiesMixin",
    "assert_different_actor",
    "assert_second_certifier_differs",
    "evaluate",
    "is_allowed",
    "require",
    "require_report",
]
