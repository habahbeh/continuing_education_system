"""
A presentable register of partners and signed agreements — **seed data, never
a business rule**.

Nothing in the project seeded ``partners.Partner`` or ``partners.Agreement``.
The only rows that ever existed were the ones test fixtures made, so a fresh
installation — and the development database a demonstration actually runs on —
opened «الشركاء المتعاقدون» on a register holding one partner left behind by a
QA run, and «الاتفاقيات» on nothing at all. Everything downstream of an
agreement (استحقاق، مطالبات، مخالصات، التزامات) had no contract to stand on,
so the walkthrough stopped at this screen.

What it writes is ordinary rows the client can read from the screens. The
names, registry numbers and contacts are invented; no figure here is referred
to by name anywhere in the code.

**Written through ``partner_service``, not through the ORM.** The service is
the write path, it audits every creation against a real actor, and it refuses
the term combinations the database refuses. A seed that went around it would
be seeding rows no rule had ever seen — and in this app one of those rules is
C-01, the demo's own live defect.

**The client's own three partners, not invented ones.** §2.4 of the
requirements names them, with their registry numbers and the model each was
signed under — مركز تناغم اللغة (312160, نسبة 50%), شركة صرح العالمية
(10200, مبلغ ثابت لكل طالب) and المثالية (بلا سجل, شريك سابق تاريخي). An
earlier version of this file invented three plausible names instead, which
made the demo register unrecognisable to the people it is shown to and put
«بيانات وهمية» on screen (polish rules §2.1).

**The terms are the signed ones**, off §3.1–3.4: تناغم at 50% with the
registration fee, the deposits and the consumables out of the divisible base,
paid at the end of each subject and settled every four months; صرح at 195
per student against a 365 sell price, paid in advance with the name list due
within a week; and the service commission of §3.3 — a 195 exam sold with 40
to the university, so 155 is the partner's share (``commission_amount`` is
what ``entitlement_service.partner_share_for`` pays out).

**The three calculation models, on purpose.** §3.4's point is that the models
are DATA and none of them is fixed in code, and a register showing three
percentage agreements would demonstrate the opposite.

**The lifecycle states are the ones the rules actually permit.** Two live
contracts to place cohorts under; an appendix left as a DRAFT so the
activation button on §3.5/24 has something to press (§3.4: «رقم الاتفاقية
لكل برنامج يتزايد كل دفعة»); and المثالية's historical contract, whose window
closed, so «انقضت مدّتها» appears somewhere other than in a test. That last
one stays a recorded draft and is NOT activated, because Sprint 8F-1 refuses
activation of a window that has already closed — a contract cannot be made
live and unusable in the same breath. Recording it is still allowed, and is
the point: a claim raised under an old contract needs that contract to exist.

    python manage.py seed_partners_demo   # idempotent
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.partners.models import (
    Agreement,
    CalculationModel,
    DiscountSplitMode,
    Partner,
    PartnerStatus,
    PartnerType,
    PayoutTiming,
    SettlementCycle,
)
from apps.partners.services import partner_service
from apps.people.models import Role, User

#: §2.4 of the requirements, verbatim: (code, name_ar, name_en, type,
#: registry number, registry date, contact, phone, email, status). The
#: registry date is the one on the paper (تناغم: 2013/10/27); the contacts
#: and phone numbers are the only invented values here, because the document
#: does not carry them and a register with no contact column filled teaches
#: nobody what the column is for.
PARTNERS: list[tuple[Any, ...]] = [
    (
        "PRT-001",
        "مركز تناغم اللغة",
        "Tanagom Language Center",
        PartnerType.COMPANY,
        "312160",
        date(2013, 10, 27),
        "أ. سامر نبيل الحوراني",
        "0796612340",
        "info@tanagom.example.jo",
        PartnerStatus.ACTIVE,
    ),
    (
        "PRT-002",
        "شركة صرح العالمية",
        "Sarh International Co.",
        PartnerType.COMPANY,
        "10200",
        None,
        "أ. هبة فايز العمري",
        "0788450012",
        "contact@sarh.example.jo",
        PartnerStatus.ACTIVE,
    ),
    (
        # §2.4 — «المثالية · — · شريك سابق (تاريخي)». No registry number on
        # the paper, and the status is the reason the register needs a
        # «سابق» chip and a status filter at all.
        "PRT-003",
        "المثالية",
        "Al-Mithaliya",
        PartnerType.COMPANY,
        "",
        None,
        "",
        "0777301188",
        "",
        PartnerStatus.FORMER,
    ),
]


class Command(BaseCommand):
    help = "زرع شركاء واتفاقيات للعرض (ثلاثة نماذج احتساب وثلاث حالات سريان)."

    def handle(self, *args: Any, **options: Any) -> None:
        # The services audit every write against a real actor, and the three
        # acts here need three different cells: ``C`` on §3.5/23, ``C`` on
        # §3.5/25 and ``A`` on §3.5/24. The centre manager holds all three.
        actor = (
            User.objects.filter(role=Role.SUPER_ADMIN, is_active=True).order_by("id").first()
            or User.objects.filter(role=Role.CENTER_MANAGER, is_active=True).order_by("id").first()
        )
        if actor is None:
            raise CommandError("لا مستخدم فعّال بدور مدير النظام أو مدير المركز ليُنسب إليه الزرع.")

        today = timezone.localdate()
        partners = self._seed_partners(actor, today)
        self._seed_agreements(actor, partners, today)

    # -- partners ----------------------------------------------------------
    def _seed_partners(self, actor: User, today: date) -> dict[str, Partner]:
        found: dict[str, Partner] = {}
        created = skipped = 0

        for row in PARTNERS:
            (
                code,
                name_ar,
                name_en,
                partner_type,
                registry_number,
                registry_date,
                contact,
                phone,
                email,
                status,
            ) = row
            # Idempotent on the code, which is what identifies a partner here.
            existing = Partner.objects.filter(code=code).first()
            if existing is not None:
                found[code] = existing
                skipped += 1
                continue

            found[code] = partner_service.create_partner(
                actor=actor,
                data={
                    "code": code,
                    "name_ar": name_ar,
                    "name_en": name_en,
                    "partner_type": partner_type,
                    "registry_number": registry_number,
                    # The date on the paper, not an offset from today: a
                    # registry entry is a fact with a date, and a demo that
                    # slides it forward every run is not showing the record.
                    "registry_date": registry_date,
                    "contact_name": contact,
                    "phone": phone,
                    "email": email,
                    "status": status,
                },
            )
            created += 1

        self.stdout.write(f"الشركاء: أُنشئ {created}، تُرك {skipped}.")
        return found

    # -- agreements --------------------------------------------------------
    def _seed_agreements(self, actor: User, partners: dict[str, Partner], today: date) -> None:
        #: (number, partner code, title, model, terms, signed offset,
        #:  from offset, to offset, activate?)
        #: (number, partner code, title, model, terms, signed offset,
        #:  from offset, to offset, activate?, supersedes number or "")
        rows: list[tuple[Any, ...]] = [
            (
                # §3.1 — تناغم: 50% of the base, with the registration fee,
                # the refundable deposits and the consumables out of it.
                # Diploma timing: paid at the end of each subject, settled
                # every four months.
                "TNG-2026/14",
                "PRT-001",
                "اتفاقية تنفيذ دبلوم اللغة الإنجليزية",
                CalculationModel.PERCENT,
                {
                    "percent_rate": Decimal("50.00"),
                    "exclude_registration_fee": True,
                    "exclude_deposits": True,
                    "exclude_consumables": True,
                    # §3.4 — «مناصفة ⟷ حسب النسبة المتفق عليها». BY_RATIO is
                    # representable only on a percentage agreement (C-01).
                    "discount_split_mode": DiscountSplitMode.BY_RATIO,
                    "payout_timing": PayoutTiming.END_OF_SUBJECT,
                    "settlement_cycle": SettlementCycle.EVERY_4_MONTHS,
                    "name_list_due_days": 30,
                    "entitlement_rule_ar": (
                        "يستحق الشريك حصته عمّا دفعه الطالب فعلاً وبحدود المبلغ "
                        "المدفوع. ولا يستحق شيئاً عن المنسحب ولا غير الحاضر ولا "
                        "غير المكمل ولا المتأخر عن الدفع."
                    ),
                },
                -210,
                -180,
                185,
                True,
                "",
            ),
            (
                # §3.4 — «رقم الاتفاقية لكل برنامج يتزايد كل دفعة (+2)»: the
                # appendix that will replace the one above. Left a DRAFT so
                # the activation button on §3.5/24 has something to press,
                # and so supersession has a case on screen.
                "TNG-2026/16",
                "PRT-001",
                "ملحق اتفاقية دبلوم اللغة الإنجليزية — الدفعة التالية",
                CalculationModel.PERCENT,
                {
                    "percent_rate": Decimal("50.00"),
                    "exclude_registration_fee": True,
                    "exclude_deposits": True,
                    "exclude_consumables": True,
                    "discount_split_mode": DiscountSplitMode.BY_RATIO,
                    "payout_timing": PayoutTiming.END_OF_SUBJECT,
                    "settlement_cycle": SettlementCycle.EVERY_4_MONTHS,
                    "name_list_due_days": 30,
                },
                -20,
                180,
                560,
                False,
                "TNG-2026/14",
            ),
            (
                # §3.2 — صرح: 195 per student against a 365 sell price, paid
                # in advance, name list due within a week of the start.
                # BY_RATIO is unrepresentable here and the seed does not try:
                # it would read 195 dinars as a percentage (C-01).
                "SRH-2026/03",
                "PRT-002",
                "اتفاقية برنامج CFM — مبلغ ثابت لكل طالب",
                CalculationModel.FIXED_PER_STUDENT,
                {
                    "fixed_amount_per_student": Decimal("195.000"),
                    "sell_price": Decimal("365.000"),
                    "exclude_registration_fee": True,
                    "exclude_deposits": True,
                    "discount_split_mode": DiscountSplitMode.HALF,
                    "payout_timing": PayoutTiming.ADVANCE,
                    "settlement_cycle": SettlementCycle.END_OF_COURSE,
                    "name_list_due_days": 7,
                    "entitlement_rule_ar": (
                        "يُصرف المبلغ مقدّماً قبل بدء الدورة، ويُسلَّم كشف الأسماء "
                        "خلال أسبوع من بدايتها؛ وما لم يُسلَّم يُسترجع بالحسم من "
                        "المطالبات اللاحقة."
                    ),
                },
                -120,
                -90,
                275,
                True,
                "",
            ),
            (
                # §3.3 — a 195 exam sold on its own with 40 to the university,
                # so 155 is the partner's share. المثالية is a FORMER partner
                # and this is the historical contract that ended; its window
                # is closed, so it is recorded and NOT activated — Sprint
                # 8F-1 refuses to make a lapsed contract live.
                "MTH-2024/07",
                "PRT-003",
                "اتفاقية خدمة — بيع امتحان دولي",
                CalculationModel.SERVICE_COMMISSION,
                {
                    "service_name_ar": "بيع امتحان دولي منفرد",
                    "service_price": Decimal("195.000"),
                    "commission_amount": Decimal("155.000"),
                    "exclude_registration_fee": True,
                    "exclude_deposits": True,
                    "discount_split_mode": DiscountSplitMode.UNIVERSITY_ONLY,
                    "payout_timing": PayoutTiming.END_OF_COURSE,
                    "settlement_cycle": SettlementCycle.END_OF_COURSE,
                },
                -760,
                -730,
                -365,
                False,
                "",
            ),
        ]

        created = skipped = activated = 0
        for number, partner_code, title, model, terms, signed, frm, until, live, after in rows:
            partner = partners.get(partner_code)
            if partner is None or Agreement.objects.filter(agreement_number=number).exists():
                skipped += 1
                continue

            data: dict[str, Any] = {
                "agreement_number": number,
                "title_ar": title,
                "signed_on": today + timedelta(days=signed),
                "valid_from": today + timedelta(days=frm),
                "valid_to": today + timedelta(days=until),
                "calculation_model": model,
                **terms,
            }
            # An appendix knows which contract it amends from the day it is
            # written; it does not END it until it is itself made live.
            if after:
                predecessor = Agreement.objects.filter(agreement_number=after).first()
                if predecessor is None:
                    skipped += 1
                    continue
                data["supersedes"] = predecessor

            with transaction.atomic():
                agreement = partner_service.create_agreement(
                    actor=actor, partner=partner, data=data
                )
                created += 1
                # Activation is its own act under its own permission, on the
                # screen and here. A lapsed window is refused by the service
                # (Sprint 8F-1), which is why the historical row stays a draft.
                if live:
                    partner_service.activate_agreement(actor=actor, agreement=agreement)
                    activated += 1

        self.stdout.write(f"الاتفاقيات: أُنشئ {created} (منها {activated} سارية)، تُرك {skipped}.")
