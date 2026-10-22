from decimal import Decimal, InvalidOperation

from app.ledger.errors import LedgerError

CENT = Decimal("0.01")


def parse_money(value: str) -> Decimal:
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError):
        raise LedgerError("invalid_amount", f"Amount {value!r} is not a decimal.", 422) from None
    if amount < 0:
        raise LedgerError("invalid_amount", "Amounts cannot be negative.", 422)
    quantized = amount.quantize(CENT)
    if quantized != amount:
        raise LedgerError("invalid_amount", "Amounts use two decimal places.", 422)
    return quantized


def money_str(amount: Decimal) -> str:
    return f"{amount.quantize(CENT):.2f}"
