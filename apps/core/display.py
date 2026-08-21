"""
Turning nullable references into display text (Sprint 8B).

Screens are full of "the approver, if there is one" and "the charge line this
allocation paid, if it paid one". Written inline, each becomes a conditional
expression the type checker cannot narrow — ``x.name if x_id else ""`` reads
as safe to a human and as a possible ``None`` attribute access to mypy.

These helpers exist so that reasoning happens once, in a place where the
nullability is the POINT rather than an aside. They take ``Any`` deliberately:
``core`` is infrastructure and may not import a business app (A-03, ADR-008),
so the shape is duck-typed rather than named.

Blank string, never ``None``, and never a placeholder like "—": a template
decides how to render an absence, and a service that guessed would take that
decision away from it.
"""

from __future__ import annotations

from typing import Any


def person_name(user: Any) -> str:
    """A user's Arabic name, falling back to their username."""
    if user is None:
        return ""
    return str(getattr(user, "full_name_ar", "") or user.get_username())


def text_of(obj: Any, attribute: str, *, default: str = "") -> str:
    """One text attribute of a possibly-absent related object."""
    if obj is None:
        return default
    value = getattr(obj, attribute, None)
    return default if value is None else str(value)


__all__ = ["person_name", "text_of"]
