"""
Verify the seals on approved claims (BR-051, layer 3).

The model's ``save()`` refuses to edit an approved claim, but it cannot see a
raw ``UPDATE`` — a queryset update, a DBA at the console, a restored backup.
This command recomputes each seal and reports the drift, which is the only
layer that survives a change made behind the ORM.

Run it on the same schedule as ``verify_audit_chain``. A claim reported here
has been altered after someone approved it; it is not a report to file away.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.settlements.models import ClaimStatus, PartnerClaim
from apps.settlements.services import claim_service


class Command(BaseCommand):
    help = "Recompute and verify the content hash of every approved claim."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--partner", type=str, default=None, help="Limit to one partner code.")

    def handle(self, *args: Any, **options: Any) -> None:
        claims = PartnerClaim.objects.filter(
            status__in=[ClaimStatus.APPROVED, ClaimStatus.PAID]
        ).prefetch_related("lines", "deductions")
        if options["partner"]:
            claims = claims.filter(partner__code=options["partner"])

        problems: list[str] = []
        checked = 0
        for claim in claims:
            checked += 1
            if not claim.content_hash:
                # The database constraint forbids this shape, so reaching it
                # means the row was written around the constraint.
                problems.append(f"{claim.code}: مطالبة معتمدة بلا ختم")
            elif not claim_service.verify_hash(claim):
                problems.append(
                    f"{claim.code}: الختم لا يطابق المحتوى — "
                    f"عُدّلت بعد الاعتماد بتاريخ {claim.approved_at:%Y-%m-%d}"
                )

        if not problems:
            self.stdout.write(
                self.style.SUCCESS(f"✓ أختام المطالبات سليمة عبر {checked} مطالبة معتمدة.")
            )
            return

        for problem in problems:
            self.stdout.write(self.style.ERROR(f"  ✗ {problem}"))
        raise CommandError(f"أختام مكسورة: {len(problems)} مطالبة من أصل {checked}.")
