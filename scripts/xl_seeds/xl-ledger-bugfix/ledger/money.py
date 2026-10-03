"""Money as integer minor units — never floats.

The ledger stores every amount as an integer number of minor units
("cents") plus a currency code. Parsing and rendering are strict: the
bank export format is ``[-]1,234.56`` (thousands separators optional, the
fractional part exactly as wide as the currency's minor units), and
rendering always emits the separators. JPY-style zero-decimal currencies
accept no fractional part at all.

Arithmetic refuses to mix currencies — a silent conversion would be the
worst possible bug in a bookkeeping tool.
"""

from __future__ import annotations

import re

from ledger.errors import ValidationError

#: Minor units per currency. The set of supported currencies is exactly
#: the keys of this table; anything else is a validation error.
DECIMALS: dict[str, int] = {"USD": 2, "EUR": 2, "GBP": 2, "JPY": 0}

#: ``1234`` or ``1,234`` or ``1,234,567`` — groups of three after the first.
_INT_PART = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d+)$")


def _decimals_for(currency: str) -> int:
    try:
        return DECIMALS[currency]
    except KeyError:
        raise ValidationError(f"unknown currency {currency!r}") from None


class Money:
    """An amount of money in integer minor units."""

    __slots__ = ("cents", "currency")

    def __init__(self, cents: int, currency: str = "USD") -> None:
        if isinstance(cents, bool) or not isinstance(cents, int):
            raise ValidationError("amount must be an integer number of cents")
        _decimals_for(currency)
        self.cents = cents
        self.currency = currency

    # ------------------------------------------------------------------ parse

    @classmethod
    def from_text(cls, text: str, currency: str = "USD") -> Money:
        """Parse a bank-style amount: ``-1,234.56``.

        Thousands separators are optional (``1234.56`` is fine) but, when
        present, must be correct groups of three. The fractional part is
        required to be exactly as wide as the currency's minor units and
        is forbidden entirely for zero-decimal currencies.
        """
        decimals = _decimals_for(currency)
        s = text.strip()
        if not s:
            raise ValidationError("empty amount")
        negative = s.startswith("-")
        if negative:
            s = s[1:]
        if "." in s:
            if decimals == 0:
                raise ValidationError(f"{currency} has no fractional part")
            int_part, _, frac_part = s.partition(".")
            if len(frac_part) != decimals:
                raise ValidationError(
                    f"expected exactly {decimals} fractional digits, got {text!r}"
                )
        else:
            int_part, frac_part = s, ""
        if not _INT_PART.match(int_part):
            raise ValidationError(f"bad integer part in amount {text!r}")
        units = int(int_part.replace(",", ""))
        cents = units * 10**decimals
        if frac_part:
            cents += int(frac_part)
        return Money(-cents if negative else cents, currency)

    def to_text(self) -> str:
        """Render with thousands separators and the currency's width."""
        decimals = _decimals_for(self.currency)
        sign = "-" if self.cents < 0 else ""
        units, frac = divmod(abs(self.cents), 10**decimals)
        if decimals:
            return f"{sign}{units:,}.{frac:0{decimals}d}"
        return f"{sign}{units:,}"

    # ------------------------------------------------------------- arithmetic

    @classmethod
    def zero(cls, currency: str = "USD") -> Money:
        return Money(0, currency)

    def is_zero(self) -> bool:
        return self.cents == 0

    def is_negative(self) -> bool:
        return self.cents < 0

    def allocate(self, weights: list[int]) -> list[Money]:
        """Split this amount by integer weights, largest remainder first.

        No cent is ever lost or created: the parts always sum to exactly
        this amount. Zero weights get nothing; an empty weight list is an
        error.
        """
        if not weights:
            raise ValidationError("allocate needs at least one weight")
        if any(w < 0 for w in weights):
            raise ValidationError("weights must be >= 0")
        total = sum(weights)
        if total == 0:
            return [Money.zero(self.currency) for _ in weights]
        sign = -1 if self.cents < 0 else 1
        absolute = abs(self.cents)
        parts = [absolute * w // total for w in weights]
        remainder = absolute - sum(parts)
        # distribute the leftover cents largest-fraction first, ties by order
        order = sorted(range(len(weights)), key=lambda i: (-(absolute * weights[i] % total), i))
        for i in order[:remainder]:
            parts[i] += 1
        return [Money(sign * p, self.currency) for p in parts]

    def _same_currency(self, other: Money) -> None:
        if other.currency != self.currency:
            raise ValidationError(f"currency mismatch: {self.currency} vs {other.currency}")

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return Money(self.cents + other.cents, self.currency)

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return Money(self.cents - other.cents, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.cents, self.currency)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self.cents == other.cents and self.currency == other.currency

    def __lt__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return self.cents < other.cents

    def __le__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return self.cents <= other.cents

    def __hash__(self) -> int:
        return hash((self.cents, self.currency))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Money({self.cents}, {self.currency!r})"

    def __str__(self) -> str:
        return f"{self.to_text()} {self.currency}"
