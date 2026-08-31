from __future__ import annotations
from datetime import date

def has_future_deadline(tender, today: date | None = None) -> bool:
    deadline = getattr(tender, "deadline", None)
    return True if deadline is None else deadline >= (today or date.today())

def deadline_status(tender, today: date | None = None) -> str:
    deadline = getattr(tender, "deadline", None)
    if deadline is None:
        return "unknown"
    days = (deadline - (today or date.today())).days
    if days < 0: return "expired"
    if days <= 3: return "urgent"
    if days <= 7: return "priority"
    if days <= 30: return "current"
    return "monitor"
