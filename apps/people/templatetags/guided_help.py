"""
``{% guided_help "enrollments" %}`` — the explanation block above a screen.

A tag rather than a context processor, because the screen a page belongs to is
not always its URL prefix: ``/operations/transfers/new/`` sits under the
transfers path and is its own screen with its own help. The template naming
itself is unambiguous, and a template that forgets the tag is caught by
``test_the_guided_help_reaches_every_screen_it_was_asked_to``.

It lives in ``people`` for the same reason ``nav`` does: it asks the permission
engine, and ADR-008 keeps ``core`` from knowing anything about that.
"""

from __future__ import annotations

from typing import Any

from django import template

from apps.people.guidance import guide_for

register = template.Library()


@register.inclusion_tag("partials/_guided_help.html", takes_context=True)
def guided_help(context: Any, key: str) -> dict[str, Any]:
    """Render this screen's help for whoever is signed in, or nothing."""
    request = context.get("request")
    return {"guide": guide_for(getattr(request, "user", None), key)}
