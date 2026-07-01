"""Money value object."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Money:
    """A monetary amount in a given ISO-4217 currency.

    Amounts are stored as ``Decimal`` to avoid float rounding on money.
    Arithmetic between different currencies is rejected.
    """

    amount: Decimal
    currency: str = "EUR"

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            object.__setattr__(self, "amount", Decimal(str(self.amount)))
        if len(self.currency) != 3 or not self.currency.isalpha():
            raise ValueError("currency must be a 3-letter ISO-4217 code")
        object.__setattr__(self, "currency", self.currency.upper())

    def _assert_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValueError(f"currency mismatch: {self.currency} vs {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._assert_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._assert_same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, factor: Decimal | int | float) -> Money:
        return Money(self.amount * Decimal(str(factor)), self.currency)

    @property
    def is_positive(self) -> bool:
        return self.amount > 0

    @classmethod
    def zero(cls, currency: str = "EUR") -> Money:
        return cls(Decimal("0"), currency)
