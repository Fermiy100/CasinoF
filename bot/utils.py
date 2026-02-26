from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any

MONEY_QUANT = Decimal("0.01")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def q_money(value: Any) -> Decimal:
    return to_decimal(value).quantize(MONEY_QUANT, rounding=ROUND_DOWN)


def fmt_money(value: Any, currency: str = "$") -> str:
    amount = q_money(value)
    return f"{currency}{amount}"

