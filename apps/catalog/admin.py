"""
Catalogue admin — the fresh-install setup door (Sprint 8F-0).

A new database has no programmes, no subjects, no price list and no
registration fee rules, and until Sprint 8F-0 the only way to get them was
``seed_catalog_demo`` — a command whose own docstring says its contents are
invented demo rows. A centre cannot open its real catalogue with a demo
fixture, so the eight catalogue tables get an admin.

**The admin answers to the permission matrix, not to ``is_staff``.**
``people/admin.py`` states the reason for its own door and it applies here
unchanged: leaving the back door on Django's default check would mean the
matrix guards the front door while the back door answers to a different rule.
So every ModelAdmin below names the SCREEN that governs its table and asks
``policy`` the same question a view would ask.

That is why this module does **not** reuse ``SystemAdministratorOnlyMixin``.
``SYSADMIN_ALLOW`` grants the system administrator two screens — users and the
audit log — and no catalogue rights at all. Handing them the catalogue here
would have invented an authority the matrix deliberately withholds. The
catalogue belongs to the centre manager (``V C E A P`` on the three programme
screens, ``V C E P`` on price lists), and that is who these screens open for.

**Three tables have no matrix row of their own** — course categories,
knowledge fields and deposit policies. They are lookups for something that
does: a category and a knowledge field classify a PROGRAMME, a deposit policy
prices a PRICE LIST ITEM. Each is therefore governed by the screen of the
thing it configures. This grants nobody anything they do not already hold over
the programme or the list itself.

**Nothing here deletes.** Every one of these tables is referenced by a
``PROTECT`` foreign key from something a participant is enrolled in or has
paid against, and retiring a row is what ``is_active`` is for. A delete button
on priced data is an offer the database would refuse and the ledger could not
survive.

**Approval is not an edit to ``status``.** ``PriceList.status`` is readonly on
every form and the only way to APPROVED is the admin action, which calls
``catalog_service.record_external_approval`` — the service that archives the
superseded list, demands the president's name and decision reference as the
only evidence the approval happened (D-31), and writes the audit row. An
approved list then freezes whole (D-14).
"""

from __future__ import annotations

from typing import Any

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import (
    CourseCategory,
    DepositPolicy,
    KnowledgeField,
    PriceList,
    PriceListItem,
    PriceListStatus,
    Program,
    RegistrationFeeRule,
    Subject,
)
from apps.catalog.services import catalog_service
from apps.core.exceptions import ImmutableRecordError
from apps.core.services.audit_service import write_audit
from apps.people.constants import Action, Screen
from apps.people.permissions import policy

#: The three programme screens. A programme's authority follows its TYPE
#: (PERMISSIONS.md §3.3 rows 9–11), and a list page shows all three at once,
#: so list-level questions are asked of the set and object-level questions of
#: the one screen that governs that row.
PROGRAM_SCREENS: tuple[str, ...] = (
    Screen.PROGRAMS,
    Screen.SHORT_COURSES,
    Screen.ONLINE_COURSES,
)


class MatrixGatedAdmin(admin.ModelAdmin):
    """
    A ModelAdmin that asks the permission matrix, and audits what it writes.

    ``screens`` is a tuple because a programme's screen depends on its type.
    ``screens_for`` narrows it to one when there is an object to narrow by;
    without one — the changelist, the add form, the module index — holding the
    action on ANY governing screen is enough to see the page, and the
    object-level check still applies to each row.
    """

    screens: tuple[str, ...] = ()
    audit_entity: str = ""

    # -- permissions ------------------------------------------------------
    def screens_for(self, obj: Any = None) -> tuple[str, ...]:
        return self.screens

    def _may(self, request: HttpRequest, action: str, obj: Any = None) -> bool:
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        return any(policy.is_allowed(user, screen, action) for screen in self.screens_for(obj))

    def has_module_permission(self, request: HttpRequest) -> bool:
        return self._may(request, Action.VIEW)

    def has_view_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return self._may(request, Action.VIEW, obj)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return self._may(request, Action.CREATE)

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return self._may(request, Action.EDIT, obj)

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """
        Never. Catalogue rows are referenced by PROTECT keys from enrolments,
        charge lines and frozen price lists; ``is_active`` retires a row
        without pretending the history did not happen.
        """
        return False

    # -- audit ------------------------------------------------------------
    def save_model(self, request: HttpRequest, obj: Any, form: Any, change: bool) -> None:
        """
        The back door leaves the same trail as the front one.

        Catalogue setup through the admin is still someone changing what the
        centre charges, and "who added this fee rule?" has to be answerable
        from ``AuditEvent`` and not from Django's own ``LogEntry``, which no
        report reads and no hash chain protects.
        """
        super().save_model(request, obj, form, change)
        write_audit(
            action="UPDATE" if change else "CREATE",
            entity_type=self.audit_entity,
            entity_id=str(obj.pk),
            reference=str(getattr(obj, "code", "") or obj.pk),
            summary_ar=("تعديل" if change else "إنشاء") + f" من لوحة الإدارة — {obj}",
            actor=request.user,
            changes={field: str(form.cleaned_data.get(field)) for field in form.changed_data},
            request=request,
        )


# ---------------------------------------------------------------------------
# Programme taxonomy
# ---------------------------------------------------------------------------
@admin.register(CourseCategory)
class CourseCategoryAdmin(MatrixGatedAdmin):
    """BR-061's eight areas — rows, because the centre may add or retire one."""

    screens = PROGRAM_SCREENS
    audit_entity = "catalog.CourseCategory"
    list_display = ("code", "name_ar", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "name_ar")


@admin.register(KnowledgeField)
class KnowledgeFieldAdmin(MatrixGatedAdmin):
    screens = PROGRAM_SCREENS
    audit_entity = "catalog.KnowledgeField"
    list_display = ("code", "name_ar", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "name_ar")


class SubjectInline(admin.TabularInline):
    """
    A diploma's subjects, beside the diploma.

    BR-006 reconciles ``Σ subject.price`` against the course fee, and a person
    entering four subjects one screen away from the total they must add up to
    will get it wrong. ``check_subject_prices`` still has the last word at
    approval; this only removes the navigation between the numbers.
    """

    model = Subject
    extra = 0
    fields = ("sequence", "name_ar", "training_hours", "price")

    # An inline does not inherit the parent's permission methods, and Django's
    # default falls back to the model permissions nobody in this system holds.
    # Without these three the subjects grid would render permanently empty.
    def _may(self, request: HttpRequest, action: str) -> bool:
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        return any(policy.is_allowed(user, screen, action) for screen in PROGRAM_SCREENS)

    def has_view_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return self._may(request, Action.VIEW)

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return self._may(request, Action.CREATE)

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return self._may(request, Action.EDIT)

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(Program)
class ProgramAdmin(MatrixGatedAdmin):
    """
    Diplomas, short courses and online courses.

    ``is_active`` stays EDITABLE, and that is a decision rather than an
    oversight. It is not a service-owned state machine: it defaults to True,
    so a programme is active from the moment it is created and a readonly
    field would protect nothing that is not already open. What it would do is
    remove the only way to retire a programme. BR-006 is not weakened by this
    — it was never gated on this flag; ``approve_program`` enforces the
    subject-price reconciliation and ``check_subject_prices`` re-checks it.
    """

    screens = PROGRAM_SCREENS
    audit_entity = "catalog.Program"
    inlines = (SubjectInline,)
    list_display = (
        "code",
        "name_ar",
        "program_type",
        "course_category",
        "is_leveled",
        "levels_count",
        "consumables_per_student",
        "is_active",
    )
    list_filter = ("program_type", "is_active", "is_leveled", "course_category")
    search_fields = ("code", "name_ar", "name_en", "specialization")

    def screens_for(self, obj: Any = None) -> tuple[str, ...]:
        if obj is None:
            return PROGRAM_SCREENS
        return (catalog_service.screen_for(obj.program_type),)


@admin.register(Subject)
class SubjectAdmin(MatrixGatedAdmin):
    """
    Reachable on its own as well as inline — a diploma with twenty subjects is
    easier to correct from a filtered list than from a form that long.
    """

    screens = PROGRAM_SCREENS
    audit_entity = "catalog.Subject"
    list_display = ("program", "sequence", "name_ar", "training_hours", "price")
    list_filter = ("program__program_type", "program")
    search_fields = ("name_ar", "program__code", "program__name_ar")
    ordering = ("program", "sequence")

    def screens_for(self, obj: Any = None) -> tuple[str, ...]:
        if obj is None:
            return PROGRAM_SCREENS
        return (catalog_service.screen_for(obj.program.program_type),)


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------
@admin.register(DepositPolicy)
class DepositPolicyAdmin(MatrixGatedAdmin):
    """
    BR-096/097. Governed by the price-list screen because a policy only ever
    reaches a participant through a price list item.
    """

    screens = (Screen.PRICELISTS,)
    audit_entity = "catalog.DepositPolicy"
    list_display = (
        "code",
        "name_ar",
        "is_required",
        "refund_trigger",
        "is_taxable",
        "allows_partial_deduction",
        "claim_deadline_days",
        "is_active",
    )
    list_filter = ("is_required", "is_taxable", "allows_partial_deduction", "is_active")
    search_fields = ("code", "name_ar", "refund_trigger")


class FrozenListChildForm(forms.ModelForm):
    """
    D-14, refused as a form error rather than as a traceback.

    ``ImmutableRecordError`` is the right answer in a service, where the
    caller is code. Here the caller is a person filling in a form, and a 500
    page tells them nothing about which field to change.
    """

    def clean_price_list(self) -> Any:
        price_list = self.cleaned_data["price_list"]
        if price_list.is_frozen:
            raise forms.ValidationError(
                f"قائمة الأسعار {price_list.code} بحالة {price_list.status} — "
                "لا تُعدَّل ولا يُضاف إليها (D-14 · BR-008). أصدر قائمة جديدة."
            )
        return price_list


class FrozenListGuardMixin:
    """
    D-14 for the two tables that hang off a price list.

    The check is on the parent, not on the row: an approved list is frozen
    whole, so its items and fee rules must be as immovable as the list itself.

    Three layers, because each catches a different move. The change permission
    closes the edit form on an existing row. The dropdown offers only lists
    that are still open, so the mistake is hard to make. The form's ``clean``
    is the one that actually refuses — a stale page or a crafted POST reaches
    it after the dropdown was rendered.
    """

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        if obj is not None and obj.price_list.is_frozen:
            return False
        return super().has_change_permission(request, obj)  # type: ignore[misc]

    def formfield_for_foreignkey(self, db_field: Any, request: HttpRequest, **kwargs: Any) -> Any:
        if db_field.name == "price_list":
            kwargs["queryset"] = PriceList.objects.filter(status=PriceListStatus.DRAFT)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)  # type: ignore[misc]


@admin.register(PriceList)
class PriceListAdmin(MatrixGatedAdmin):
    """
    ``status`` is readonly on every form, always.

    Approval is a state transition with consequences the admin cannot carry
    out by assignment: the previously approved list for the semester has to be
    archived, the president's name and decision reference have to be present,
    and the whole thing has to be audited. Typing APPROVED into a dropdown
    would do none of that, and would then trip
    ``catalog_price_list_one_approved_per_semester`` as an IntegrityError
    rather than as a message anyone could act on. The action below is the way.
    """

    screens = (Screen.PRICELISTS,)
    audit_entity = "catalog.PriceList"
    actions = ("record_president_approval",)
    list_display = (
        "code",
        "name_ar",
        "semester",
        "status",
        "issued_on",
        "effective_from",
        "approved_by_text",
        "decision_reference",
        "approved_at",
    )
    list_filter = ("status", "semester")
    search_fields = ("code", "name_ar", "decision_reference")

    #: Set by the system, never by a form — ``approved_at`` and ``archived_at``
    #: are stamped by the service, and ``status`` is the transition itself.
    SYSTEM_OWNED = ("status", "approved_at", "archived_at")

    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> tuple[str, ...]:
        if obj is not None and obj.is_frozen:
            # D-14 — an approved or archived list is read-only in every field,
            # not merely in the ones that made it approved.
            return tuple(
                field.name
                for field in self.model._meta.fields
                if field.name != "id" and field.editable
            )
        return self.SYSTEM_OWNED

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        if obj is not None and obj.is_frozen:
            # Still viewable — the freeze hides the save button, not the list.
            return False
        return super().has_change_permission(request, obj)

    @admin.action(description=_("تسجيل اعتماد رئيس الجامعة (BR-008 · D-31)"))
    def record_president_approval(self, request: HttpRequest, queryset: QuerySet) -> None:
        """
        Calls ``catalog_service.record_external_approval`` — the service that
        already existed.

        No ``approve_price_list`` was added beside it. The service's own
        docstring explains why it is spelled the way it is: nobody in the
        matrix holds ``A`` over a price list, because the approver is the
        university president and they have no account here (D-31). A second
        function with an approving name would have invited exactly the
        unevidenced approval the first one refuses.

        The approver's name and the decision reference are read from the row.
        They are ordinary editable fields while the list is a draft, which is
        the point: the evidence is entered, reviewed, and only then acted on.
        """
        approved, refused = 0, 0
        for price_list in queryset:
            try:
                catalog_service.record_external_approval(
                    actor=request.user,
                    price_list=price_list,
                    approved_by_text=price_list.approved_by_text,
                    decision_reference=price_list.decision_reference,
                    request=request,
                )
            except (PermissionDenied, ValidationError, ImmutableRecordError) as refusal:
                refused += 1
                reason = getattr(refusal, "messages", None) or [str(refusal)]
                self.message_user(request, f"{price_list.code}: {reason[0]}", level=messages.ERROR)
            else:
                approved += 1
        if approved:
            self.message_user(
                request,
                f"اعتُمدت {approved} قائمة أسعار — وأُرشفت أي قائمة سابقة لنفس الفصل (BR-008).",
                level=messages.SUCCESS,
            )
        if refused and not approved:
            self.message_user(request, "لم تُعتمد أي قائمة.", level=messages.WARNING)


@admin.register(PriceListItem)
class PriceListItemAdmin(FrozenListGuardMixin, MatrixGatedAdmin):
    form = FrozenListChildForm
    screens = (Screen.PRICELISTS,)
    audit_entity = "catalog.PriceListItem"
    list_display = (
        "price_list",
        "program",
        "level",
        "course_fee",
        "deposit_amount",
        "deposit_policy",
    )
    list_filter = ("price_list", "program__program_type", "deposit_policy")
    search_fields = ("program__code", "program__name_ar", "price_list__code")
    autocomplete_fields = ("program", "deposit_policy")


@admin.register(RegistrationFeeRule)
class RegistrationFeeRuleAdmin(FrozenListGuardMixin, MatrixGatedAdmin):
    """
    BR-009/Q-10. ``fee`` empty means NO registration fee, which the model's
    docstring distinguishes from a fee of zero — the changelist shows the
    blank rather than a 0.000 so the two stay distinguishable on screen too.
    """

    form = FrozenListChildForm
    screens = (Screen.PRICELISTS,)
    audit_entity = "catalog.RegistrationFeeRule"
    list_display = ("price_list", "program", "participant_category", "fee", "exception_note_ar")
    list_filter = ("price_list", "participant_category")
    search_fields = ("participant_category", "program__code", "exception_note_ar")
    autocomplete_fields = ("program",)


__all__ = [
    "CourseCategoryAdmin",
    "DepositPolicyAdmin",
    "FrozenListChildForm",
    "KnowledgeFieldAdmin",
    "MatrixGatedAdmin",
    "PriceListAdmin",
    "PriceListItemAdmin",
    "ProgramAdmin",
    "RegistrationFeeRuleAdmin",
    "SubjectAdmin",
]
