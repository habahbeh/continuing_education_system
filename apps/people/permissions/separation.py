"""
Separation of duties — D-18 and D-30.

D-18 ("nobody approves what they created") is the one control in
PERMISSIONS.md §4 that has no counterpart in the demo at all. With six users
and six roles it is trivially easy for the finance officer to raise a refund,
approve it and pay it. D-30 is the same idea applied to the two signatures on
the financial clearance step (Q-14): the second certifier must not be the first.

The entities these guard — Discount, Refund, PartnerClaim, DailyClosing,
OpeningBalance, ClearanceStep — arrive in Sprints 4 through 8. The check is
built and tested here, now, so that four later sprints inherit one implementation
instead of writing four subtly different ones.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


def _pk(actor: Any) -> Any:
    return getattr(actor, "pk", actor)


def assert_different_actor(
    first: Any,
    second: Any,
    *,
    rule_id: str,
    message: str,
) -> None:
    """Raise ValidationError when the same person fills both sides of a control."""
    if first is None or second is None:
        return
    if _pk(first) == _pk(second):
        raise ValidationError(f"{message} ({rule_id})", code=rule_id)


class SeparationOfDutiesMixin(models.Model):
    """
    Mixin for any model where one actor creates and another approves (D-18).

    Subclasses declare the two field names. ``clean()`` enforces the rule, and
    the owning sprint adds the matching CheckConstraint — because a rule
    enforced only in Python is a rule that a management command or a bulk
    update walks straight past.
    """

    #: Field holding the actor who created the record.
    sod_creator_field: str = "created_by"
    #: Field holding the actor who approved it.
    sod_approver_field: str = "approved_by"
    #: Deny rule reported when the two coincide.
    sod_rule_id: str = "D-18"
    sod_message = _("لا يمكنك اعتماد ما أنشأته بنفسك")

    class Meta:
        abstract = True

    def clean(self) -> None:
        super().clean()
        assert_different_actor(
            getattr(self, f"{self.sod_creator_field}_id", None),
            getattr(self, f"{self.sod_approver_field}_id", None),
            rule_id=self.sod_rule_id,
            message=str(self.sod_message),
        )


def assert_second_certifier_differs(first_certifier: Any, second_certifier: Any) -> None:
    """
    D-30 — the second certification of the financial clearance step (Q-14).

    BR-074 requires two signatures on step 2. Two signatures from one person is
    not a dual control; it is a single control wearing a costume.
    """
    assert_different_actor(
        first_certifier,
        second_certifier,
        rule_id="D-30",
        message="لا يجوز أن يكون المصادق الثاني هو المصادق الأول",
    )


__all__ = [
    "SeparationOfDutiesMixin",
    "assert_different_actor",
    "assert_second_certifier_differs",
]
