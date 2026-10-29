"""Nächste Inspektion aus letzter Inspektion + Intervall (Monate/km) berechnen."""
from __future__ import annotations

from calendar import monthrange
from datetime import date
from typing import Any

from .config import Vehicle


def add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    return date(y, m, min(d.day, monthrange(y, m)[1]))


def compute_status(v: Vehicle, last: dict[str, Any] | None, odometer_km: int | None,
                   today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    empty = {"status": "unbekannt", "next_date": None, "days_left": None,
             "next_km": None, "km_left": None, "progress": None}
    if not last:
        return empty

    base_date, base_km = last["date"], last.get("km")
    next_date = add_months(base_date, v.interval_months) if v.interval_months else None
    days_left = (next_date - today).days if next_date else None
    next_km = base_km + v.interval_km if (v.interval_km and base_km is not None) else None
    km_left = next_km - odometer_km if (next_km is not None and odometer_km is not None) else None

    fractions = []
    if next_date:
        fractions.append((today - base_date).days / max((next_date - base_date).days, 1))
    if km_left is not None:
        fractions.append((odometer_km - base_km) / v.interval_km)
    progress = round(max(0.0, min(max(fractions), 1.0)) * 100) if fractions else None

    overdue = (days_left is not None and days_left < 0) or (km_left is not None and km_left < 0)
    soon = (days_left is not None and days_left <= v.warn_days) or (km_left is not None and km_left <= v.warn_km)
    return {
        "status": "überfällig" if overdue else "bald fällig" if soon else "ok",
        "next_date": next_date, "days_left": days_left,
        "next_km": next_km, "km_left": km_left, "progress": progress,
    }
