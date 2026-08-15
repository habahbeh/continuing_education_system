"""Domain exceptions for the core layer."""

from __future__ import annotations


class CoreError(Exception):
    """Base class for core domain errors."""


class ImmutableRecordError(CoreError):
    """
    Raised when code attempts to modify or delete an append-only record.

    Applies to AuditEvent (BR-084 / ADR-010) and, from Sprint 4 onward, to
    issued receipts, approved claims, approved price lists and reconciled
    daily closings (D-11 … D-16).
    """


class SettingNotFound(CoreError):
    """Raised when an EffectiveSetting has no value in effect on a given date."""


class SequenceExhausted(CoreError):
    """Raised when a NumberSequence cannot produce another value."""


class ConcurrencyRetryExhausted(CoreError):
    """
    Raised when an operation kept losing to lock contention.

    A MySQL deadlock aborts the whole transaction, so the retry has to live at
    the service that owns it — the numbering layer cannot retry on a caller's
    behalf. This is what the caller raises once its own attempts run out, and
    it is deliberately distinct from OperationalError so the caller's exhausted
    retry is not mistaken for a first failure.
    """


class AuditChainBroken(CoreError):
    """Raised when the audit hash chain fails verification (ADR-010, layer 3)."""


class TaxRateNotConfigured(CoreError):
    """
    Raised when a taxable charge is created while ``default_tax_rate`` is unset.

    Q-05 (revised): the client confirmed the university accounts for revenue
    with tax, but the rate itself is still open (Q-25). ``default_tax_rate``
    is seeded as NULL, meaning "rate not yet known" — NOT "no tax".

    Sprint 4 must raise this rather than silently computing zero tax; a silent
    zero would turn a known blocking question into an invisible data error.
    Defined here in Sprint 1 so the intent is recorded at the foundation.
    """


__all__ = [
    "AuditChainBroken",
    "ConcurrencyRetryExhausted",
    "CoreError",
    "ImmutableRecordError",
    "SequenceExhausted",
    "SettingNotFound",
    "TaxRateNotConfigured",
]
