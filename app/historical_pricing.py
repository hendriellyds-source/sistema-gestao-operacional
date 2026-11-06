"""Keep explicitly linked historical drafts in sync with work registration rates."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def _amount(value):
    try:
        amount = Decimal(str(value or 0))
        return amount if amount.is_finite() and amount > 0 else Decimal(0)
    except (InvalidOperation, ValueError):
        return Decimal(0)


def refresh_historical_prices(state):
    works = {str(item.get("id")): item for item in state.get("works", [])}
    invoiced = {str(identifier) for invoice in state.get("invoices", [])
                for identifier in (invoice.get("scheduleEntryIds") or [])}
    for row in state.get("workScheduleEntries", []):
        if not row.get("historical") or str(row.get("id")) in invoiced:
            continue
        if row.get("historicalPriceLinked") is not True and _amount(row.get("value")):
            continue
        row["historicalPriceLinked"] = True
        work = works.get(str(row.get("workId")), {})
        if str(work.get("companyId")) != str(row.get("companyId")):
            work = {}
        full, half = _amount(work.get("dailyRate")), _amount(work.get("halfDailyRate"))
        value = (half or full / 2) if row.get("dailyType") == "Meia" else full if row.get("dailyType") == "Inteira" else Decimal(0)
        value = float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        if _amount(row.get("value")) != Decimal(str(value)):
            row.update(value=value, reviewed=False, historicalPending=True)
        if not value:
            row["historicalPending"] = True
