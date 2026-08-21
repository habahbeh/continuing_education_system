"""
Every word on a printed document, read from settings (Sprint 8C-1).

**Why none of this is a literal in a template.** The centre's controlled forms
were not available when these were built: the client folder carries the signed
agreements and the 2026 price list, and no blank form of the centre's own. So
the layout follows what §6.4 and §7 actually specify, and every label, code,
revision and line of wording is DATA — because the first thing that will
happen when the real form arrives is that half of them turn out to be worded
differently.

``document_mode`` is the honesty marker. Seeded ``REQUIREMENTS_BASED``, it
makes every document print a footer saying so. Changing it to ``OFFICIAL``
once the printed output has actually been checked against the centre's form is
a dated setting change — and until someone does that, no document claims to be
something it has not been verified as.

``core`` may hold this because it reads settings and imports no business app
(A-03). The clearance and certificate services call it; neither owns it,
because both print on the same paper under the same letterhead.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from apps.core.services.settings_service import get_setting

# --- keys ------------------------------------------------------------------
MODE_KEY = "document_mode"
UNIVERSITY_KEY = "document_university_ar"
CENTER_KEY = "document_center_ar"
FOOTER_KEY = "document_footer_ar"
UNVERIFIED_NOTE_KEY = "document_unverified_note_ar"

CLEARANCE_CODE_KEY = "clearance_form_code"
CLEARANCE_TITLE_KEY = "clearance_form_title_ar"
CUSTODY_ITEMS_KEY = "clearance_custody_items"
CLEARANCE_SIGNATURES_KEY = "clearance_signature_labels"

CERTIFICATE_TITLE_KEY = "certificate_title_ar"
CERTIFICATE_BODY_KEY = "certificate_body_ar"
CERTIFICATE_REPLACEMENT_NOTE_KEY = "certificate_replacement_note_ar"
CERTIFICATE_SIGNATURES_KEY = "certificate_signature_labels"
CERTIFICATE_STAMPS_KEY = "certificate_stamp_labels"

#: The two modes a document can be printed under.
MODE_REQUIREMENTS = "REQUIREMENTS_BASED"
MODE_OFFICIAL = "OFFICIAL"


def _text(key: str, *, as_of: date, default: str = "") -> str:
    value = get_setting(key, as_of=as_of, default=default)
    return "" if value is None else str(value)


def _list(key: str, *, as_of: date) -> list[str]:
    """
    A JSON list stored as a string, the way ``certificate_grades`` already is.

    A malformed value yields an empty list rather than an exception: a
    mistyped setting should leave a signature line off a page, not stop the
    centre printing the document at all.
    """
    raw = get_setting(key, as_of=as_of, default=None)
    if not raw:
        return []
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def document_mode(*, as_of: date) -> str:
    """``REQUIREMENTS_BASED`` until someone verifies against the real form."""
    return _text(MODE_KEY, as_of=as_of, default=MODE_REQUIREMENTS).upper()


def is_verified(*, as_of: date) -> bool:
    return document_mode(as_of=as_of) == MODE_OFFICIAL


def letterhead(*, as_of: date) -> dict[str, str]:
    """The header every printed document shares."""
    return {
        "university_ar": _text(UNIVERSITY_KEY, as_of=as_of, default="جامعة البترا"),
        "center_ar": _text(CENTER_KEY, as_of=as_of, default="مركز التعليم المستمر وخدمة المجتمع"),
    }


def chrome(*, as_of: date) -> dict[str, Any]:
    """
    Everything a print template needs that is not the document's own content.
    """
    verified = is_verified(as_of=as_of)
    return {
        **letterhead(as_of=as_of),
        "mode": document_mode(as_of=as_of),
        "is_verified": verified,
        "footer_ar": _text(FOOTER_KEY, as_of=as_of),
        # Shown only while the layout has not been checked against the
        # centre's own form. It is the difference between a document and a
        # draft of one, and the reader is entitled to know which they hold.
        "unverified_note_ar": "" if verified else _text(UNVERIFIED_NOTE_KEY, as_of=as_of),
    }


def clearance_labels(*, as_of: date) -> dict[str, Any]:
    """
    Titles and signature lines for the clearance form.

    ``form_code`` is seeded ``CS Fm 7.18 Rev A`` because §6.4 names it. That
    the code is right does not make the LAYOUT right, which is what
    ``document_mode`` is for.
    """
    return {
        "form_code": _text(CLEARANCE_CODE_KEY, as_of=as_of, default="CS Fm 7.18 Rev A"),
        "title_ar": _text(CLEARANCE_TITLE_KEY, as_of=as_of, default="نموذج براءة ذمة"),
        "signature_labels": _list(CLEARANCE_SIGNATURES_KEY, as_of=as_of),
    }


def custody_items(*, as_of: date) -> list[str]:
    """
    The standard list §6.4 names — «هوية المركز» and «بطاقة المواصلات».

    Deferred out of Sprint 8B-2 and landed here, where the form it belongs to
    is being built. An empty setting means the centre has not defined a
    standard list, and the service treats that as "type what was returned"
    rather than as an error.
    """
    return _list(CUSTODY_ITEMS_KEY, as_of=as_of)


def certificate_labels(*, as_of: date) -> dict[str, Any]:
    """
    Wording for the certificate — §7's seven fields wrapped in the centre's
    own sentence, plus the manual signature and stamp placeholders.

    §7 is explicit that the signature and both stamps are applied BY HAND, so
    what prints is the empty space they go in, correctly labelled.
    """
    return {
        "title_ar": _text(CERTIFICATE_TITLE_KEY, as_of=as_of, default="شهادة"),
        "body_ar": _text(CERTIFICATE_BODY_KEY, as_of=as_of),
        "replacement_note_ar": _text(CERTIFICATE_REPLACEMENT_NOTE_KEY, as_of=as_of),
        "signature_labels": _list(CERTIFICATE_SIGNATURES_KEY, as_of=as_of),
        "stamp_labels": _list(CERTIFICATE_STAMPS_KEY, as_of=as_of),
    }


__all__ = [
    "CUSTODY_ITEMS_KEY",
    "MODE_KEY",
    "MODE_OFFICIAL",
    "MODE_REQUIREMENTS",
    "certificate_labels",
    "chrome",
    "clearance_labels",
    "custody_items",
    "document_mode",
    "is_verified",
    "letterhead",
]
