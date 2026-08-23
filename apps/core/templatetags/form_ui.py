"""
Presentation helpers for rendering a form field. Nothing here is business
logic, and nothing here can change what a form accepts.

The one thing this module does that a template cannot: put attributes on the
control itself. ``{{ field }}`` renders the widget with the attributes the
form declared and no way to add to them, so ``_form.html`` had no means of
emitting ``aria-invalid`` on a rejected field or of pointing the control at
its own help and error text. It therefore emitted neither — and it dropped
``help_text`` entirely, which quietly discarded twenty-eight sentences that
explain a rule (BR-092, BR-054, «اتركه فارغاً للرصيد المُقيَّد من ملف ورقي»)
to the person filling the field in.

``as_widget(attrs=...)`` merges through ``Widget.build_attrs``: the widget's
own attributes are preserved and these are added to them. Field names, widget
types, ``required``, validation and the rendered ``type="date"`` guarantee are
all untouched.

A-03: this module imports Django and nothing else — apps.core depends on no
other local app.
"""

from __future__ import annotations

from django import template
from django.forms import BoundField

register = template.Library()


def _describedby_ids(field: BoundField) -> list[str]:
    """The ids of the text that describes this control, in reading order."""
    if not field.auto_id:
        # A form built with auto_id=False has no stable id to point at, so
        # there is nothing to reference and the association is skipped.
        return []
    ids: list[str] = []
    if field.help_text:
        ids.append(f"{field.auto_id}_help")
    if field.errors:
        ids.append(f"{field.auto_id}_err")
    return ids


@register.simple_tag
def field_control(field: BoundField) -> str:
    """
    Render the widget, wired to its own help and error text.

    Returns exactly what ``{{ field }}`` returns, plus ``aria-describedby``
    when there is describing text and ``aria-invalid`` when the field was
    rejected. The ids match the ones ``field_help_id`` and ``field_error_id``
    put on the corresponding elements.
    """
    # `str | bool` is what BoundField.as_widget accepts: Django puts boolean
    # attributes (required, disabled) through the same dict.
    attrs: dict[str, str | bool] = {}
    ids = _describedby_ids(field)
    if ids:
        attrs["aria-describedby"] = " ".join(ids)
    if field.errors:
        attrs["aria-invalid"] = "true"
    return field.as_widget(attrs=attrs or None)


@register.simple_tag
def field_help_id(field: BoundField) -> str:
    return f"{field.auto_id}_help" if field.auto_id else ""


@register.simple_tag
def field_error_id(field: BoundField) -> str:
    return f"{field.auto_id}_err" if field.auto_id else ""


@register.filter
def is_checkbox(field: BoundField) -> bool:
    """
    True for a single on/off control.

    ``.fld input`` is ``w-full``, so before this a checkbox stretched the
    width of its grid column with its label stranded above it. A checkbox
    belongs on one line with its label, sharing a single hit target.

    ``CheckboxSelectMultiple`` is deliberately excluded: it renders a list of
    inputs, not one control, and the single-line layout would be wrong for it.
    """
    widget = field.field.widget
    return getattr(widget, "input_type", None) == "checkbox" and not getattr(
        widget, "allow_multiple_selected", False
    )
