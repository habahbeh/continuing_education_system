"""
The partner half of §3, executed rather than merely configured — **seed data,
never a business rule**.

``seed_partners_demo`` put the client's own partners and their signed terms on
the register. Nothing then used them: a survey of the demo database found
**four agreements, four partners, and not one cohort carrying an agreement** —
``Cohort.agreement`` was null on all six. So the whole partner half of the
system (2286 lines of service across seven models, and «كشف مستحقات كل شريك
ومخالصاته», report 3 of §9) stood on data that never reached it, and that
report opened on an empty state.

The link was never missing from the software — both the cohort creation form
and the row editor carry «الاتفاقية». It was missing from the data.

**Which agreement governs which cohort — by MODEL, not by programme name.**
§3.4's point is that the calculation models are data and none is fixed in
code, and §3.1 itself distinguishes them by programme SHAPE rather than by
title: a diploma settles every four months at the end of each subject, a short
course settles at the end of the course. So:

* **دبلوم التصميم الداخلي → تناغم (TNG-2026/14)** — نسبة 50%, end of each
  subject, settled every four months. §3.1's exact shape.
* **دورة هندسة الشبكات → صرح (SRH-2026/03)** — 195 per student against a 365
  sell price, paid in advance, name list due within a week. §3.2's exact shape.

The agreements' own titles name other programmes, because they are the
client's real signed contracts and the demo catalogue is not. The pairing here
is by model on purpose, and nothing in the code reads either title.

**The short course is the one that demonstrates §5.4.** Its five enrolments
are two COMPLETED, one CANCELLED, one DISMISSED and one WITHDRAWN — so the
claim it raises carries eligible lines beside excluded ones, each with the
reason it earned nothing (BR-045). A cohort where everyone qualified would
demonstrate the opposite of the rule.

**The diploma's claim is expected to be near nil, and that is the point.**
Nothing has been collected on it yet, and §5.4 entitles a partner only to what
the student actually paid. A percentage of nothing is nothing, said out loud
on the screen rather than hidden by seeding a payment that never happened.

**Written through the services, not the ORM** — ``cohort_service`` for the
link, ``claim_service`` for the claim, ``settlement_service`` for the cycle.
Each audits against a real actor and refuses what the rules refuse; rows
written around them would be rows no rule had ever seen.

**And through TWO actors, because one cannot do this.** The matrix separates
the duties: the finance officer holds ``C`` on «المطالبات» and «المخالصات»
and the centre manager holds ``A``, and neither holds the other's. Seeding
this chain under a single super-administrator would produce rows that no real
workflow could produce — a claim raised and approved by the same hand — which
is the opposite of what a demonstration is for. So the finance officer builds
and the manager approves, exactly as they would on the screens.

    python manage.py seed_settlements_demo   # idempotent
"""

from __future__ import annotations

from datetime import date
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.operations.models import Cohort
from apps.partners.models import Agreement
from apps.people.models import Role, User
from apps.settlements.models import PartnerClaim, PartnerSettlement

#: (cohort code, agreement number, why this pairing) — see the module docstring.
PAIRINGS: tuple[tuple[str, str, str], ...] = (
    ("CO-DIP-ID-1", "TNG-2026/14", "دبلوم بنموذج النسبة — §3.1"),
    ("CO-QA-1", "SRH-2026/03", "دورة قصيرة بنموذج المبلغ الثابت — §3.2"),
)


class Command(BaseCommand):
    help = "ربط الدفعات باتفاقياتها ورفع مطالبة ومخالصة للعرض (§3 · §5.4 · تقرير ٩/٣)."

    def handle(self, *args: Any, **options: Any) -> None:
        manager = self._actor(Role.CENTER_MANAGER, "مدير المركز")
        finance = self._actor(Role.FINANCE_OFFICER, "الموظف المالي")

        today = timezone.localdate()
        linked = self._link_cohorts(manager)
        self._raise_claims(finance, manager, linked, today)

    def _actor(self, role: str, label: str) -> User:
        found = User.objects.filter(role=role, is_active=True).order_by("id").first()
        if found is None:
            raise CommandError(f"لا مستخدم فعّال بدور {label} ليُنسب إليه الزرع.")
        return found

    # -- the link that was missing -----------------------------------------
    def _link_cohorts(self, actor: User) -> list[tuple[Cohort, Agreement]]:
        from apps.operations.services import cohort_service

        pairs: list[tuple[Cohort, Agreement]] = []
        attached = kept = 0
        for cohort_code, agreement_number, why in PAIRINGS:
            cohort = Cohort.objects.filter(code=cohort_code).select_related("agreement").first()
            agreement = Agreement.objects.filter(agreement_number=agreement_number).first()
            if cohort is None or agreement is None:
                self.stdout.write(
                    f"  تُخطّيت {cohort_code} ← {agreement_number}: "
                    f"{'لا دفعة بهذا الرمز' if cohort is None else 'لا اتفاقية بهذا الرقم'}."
                )
                continue
            if cohort.agreement_id == agreement.pk:
                kept += 1
            else:
                cohort_service.update_cohort(
                    actor=actor, cohort=cohort, data={"agreement_number": agreement_number}
                )
                cohort.refresh_from_db()
                attached += 1
                self.stdout.write(f"  {cohort_code} ← {agreement_number} ({why})")
            pairs.append((cohort, agreement))
        self.stdout.write(f"الربط: رُبطت {attached}، تُركت {kept} كما هي.")
        return pairs

    # -- one claim per pairing, and a cycle for the one that carries money --
    def _raise_claims(
        self,
        finance: User,
        manager: User,
        pairs: list[tuple[Cohort, Agreement]],
        today: date,
    ) -> None:
        from apps.settlements.services import claim_service

        raised = kept = 0
        for cohort, agreement in pairs:
            existing = PartnerClaim.objects.filter(agreement=agreement, cohort=cohort).first()
            if existing is not None:
                kept += 1
                self._report(existing)
                continue

            claim = claim_service.build_claim(
                actor=finance,
                agreement=agreement,
                cohort=cohort,
                period_from=cohort.starts_on,
                period_to=min(cohort.ends_on, today),
                trigger_type="END_OF_COURSE",
                trigger_reference_ar=f"مطالبة عرض عن {cohort.name_ar}",
            )
            claim_service.apply_offsets(actor=finance, claim=claim)
            # The approval is the manager's: §8 gives the centre manager the
            # approving hand and the finance officer the building one.
            claim_service.approve_claim(actor=manager, claim=claim)
            claim.refresh_from_db()
            raised += 1
            self._report(claim)

            # A cycle is worth opening only where something is actually owed;
            # an empty settlement is a document that says nothing.
            if claim.net_payable and claim.net_payable > 0:
                self._settle(finance, agreement, cohort, claim, today)

        self.stdout.write(f"المطالبات: رُفعت {raised}، وُجدت {kept}.")

    def _settle(
        self, actor: User, agreement: Agreement, cohort: Cohort, claim: Any, today: date
    ) -> None:
        from apps.settlements.services import settlement_service

        if PartnerSettlement.objects.filter(agreement=agreement).exists():
            return
        code = f"ST-{agreement.partner.code}-{today:%Y%m}"
        settlement = settlement_service.open_settlement(
            actor=actor, agreement=agreement, opens_on=today, code=code, cohort=cohort
        )
        attached = settlement_service.attach_claims(actor=actor, settlement=settlement)
        settlement.refresh_from_db()
        self.stdout.write(
            f"  مخالصة {code}: {len(attached)} مطالبة · مستحق {settlement.total_due}"
        )

    def _report(self, claim: Any) -> None:
        lines = claim.lines.count()
        excluded = claim.lines.filter(is_included=False).count() if lines else 0
        self.stdout.write(
            f"  مطالبة {claim.code}: بنود {lines} (مستبعَد {excluded}) · "
            f"صافي {claim.net_payable} · {claim.status}"
        )
