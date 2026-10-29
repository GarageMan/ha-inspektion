"""Add-on-Hauptprozess.

- Abruf monatlich (schedule_day/schedule_time) und beim Start, wenn Daten älter als max_age_days
- Sofortabruf per Dienst hassio.addon_stdin mit input "refresh" (oder "refresh <id>")
- Sensoren alle 10 Minuten aus dem Cache neu setzen (überlebt HA-Neustarts, km-Stand bleibt aktuell)
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import date, datetime, timedelta
from typing import Any

from . import __version__
from .config import DATA_DIR, Settings, Vehicle, load_settings
from .ha import HomeAssistant
from .parser import parse
from .planner import compute_status

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
log = logging.getLogger("inspektion")

CACHE_PATH = DATA_DIR / "cache.json"
PUSH_EVERY = timedelta(minutes=10)
STATUS_ICON = {"ok": "mdi:check-circle", "bald fällig": "mdi:alert", "überfällig": "mdi:alert-octagon",
               "unbekannt": "mdi:help-circle"}


# ---------------------------------------------------------------- Cache
def load_cache() -> dict[str, Any]:
    if not CACHE_PATH.is_file():
        return {}
    data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    for item in data.values():
        for e in item.get("entries", []):
            e["date"] = date.fromisoformat(e["date"])
    return data


def save_cache(cache: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(CACHE_PATH)


# ---------------------------------------------------------------- Zeitplan
def next_run(settings: Settings, after: datetime) -> datetime:
    hh, mm = map(int, settings.schedule_time.split(":"))
    candidate = after.replace(day=settings.schedule_day, hour=hh, minute=mm, second=0, microsecond=0)
    if candidate <= after:
        month = after.month % 12 + 1
        year = after.year + (1 if after.month == 12 else 0)
        candidate = candidate.replace(year=year, month=month)
    return candidate


# ---------------------------------------------------------------- Abruf
async def refresh_vehicle(v: Vehicle, settings: Settings, cache: dict[str, Any]) -> None:
    from .scraper import ScrapeError, fetch_vehicle  # Playwright erst bei Bedarf

    item = cache.setdefault(v.id, {})
    item["last_try"] = datetime.now().isoformat(timespec="seconds")
    for attempt in (1, 2):
        try:
            raw = await fetch_vehicle(v, debug=settings.debug_dump)
            parsed = parse(raw, v.vin)
            if parsed["login_error"]:
                raise ScrapeError(parsed["login_error"])
            if not parsed["entries"]:
                raise ScrapeError("Angemeldet, aber keine Inspektion erkannt – debug_dump aktivieren.")
            item.update({
                "entries": parsed["entries"][:20], "model": parsed["model"],
                "recall_open": parsed["recall_open"], "extras": parsed["extras"], "error": None,
                "last_ok": datetime.now().isoformat(timespec="seconds"),
            })
            last = parsed["entries"][0]
            log.info("%s: %s – letzte Inspektion %s, %s km, %s (%d Einträge, %d Detailfelder)",
                     v.id, parsed["model"] or "Modell ?", last["date"], last["km"], last["dealer"] or "Händler ?",
                     len(parsed["entries"]), len([k for k in last["details"] if not k.startswith("_")]))
            return
        except Exception as exc:  # noqa: BLE001
            msg = (str(exc).splitlines() or [exc.__class__.__name__])[0][:250]
            log.warning("%s: Versuch %d fehlgeschlagen: %s", v.id, attempt, msg)
            item["error"] = msg
            if "Login fehlgeschlagen" in msg or "FIN" in msg:
                break  # falsche Zugangsdaten nicht wiederholen (Kontosperre vermeiden)
            if attempt == 1:
                await asyncio.sleep(30)


async def refresh(settings: Settings, cache: dict[str, Any], only: str | None = None) -> None:
    for v in settings.vehicles:  # nacheinander, schonend
        if only and v.id != only:
            continue
        await refresh_vehicle(v, settings, cache)
    save_cache(cache)


# ---------------------------------------------------------------- Sensoren
def _iso(d: Any) -> str | None:
    return d.isoformat() if isinstance(d, (date, datetime)) else d


async def push_states(ha: HomeAssistant, settings: Settings, cache: dict[str, Any], next_at: datetime) -> None:
    for v in settings.vehicles:
        item = cache.get(v.id, {})
        entries = item.get("entries", [])
        last = entries[0] if entries else None
        odo = await ha.get_number(v.odometer_entity)
        portal_km = max((e["km"] for e in entries if e.get("km") is not None), default=None)
        if portal_km is not None and (odo is None or portal_km > odo):
            odo = portal_km
        plan = compute_status(v, last, odo)
        base = f"sensor.inspektion_{v.id}"
        common = {"fahrzeug": v.name, "marke": v.brand, "modell": item.get("model"), "fin": v.vin_masked,
                  "portal": v.url, "abgerufen_am": item.get("last_ok"), "letzter_versuch": item.get("last_try"),
                  "fehler": item.get("error")}

        details = {k: val for k, val in (last.get("details", {}) if last else {}).items() if not k.startswith("_")}
        await ha.set_state(f"{base}_letzte", _iso(last["date"]) if last else None, {
            **common,
            **(item.get("extras") or {}),
            "friendly_name": f"{v.name} – letzte Inspektion",
            "device_class": "date", "icon": "mdi:wrench-clock",
            "kilometerstand": last.get("km") if last else None,
            "leistung": last.get("description") if last else None,
            "haendler": last.get("dealer") if last else None,
            "details": details,
            "quelle": last.get("source") if last else None,
            "historie": [{"datum": _iso(e["date"]), "km": e.get("km"), "leistung": e.get("description"),
                          "haendler": e.get("dealer")} for e in entries[:10]],
        })
        await ha.set_state(f"{base}_naechste", _iso(plan["next_date"]), {
            "friendly_name": f"{v.name} – nächste Inspektion",
            "device_class": "date", "icon": "mdi:calendar-clock",
            "tage_bis": plan["days_left"], "faellig_bei_km": plan["next_km"], "rest_km": plan["km_left"],
            "kilometerstand_aktuell": odo, "fortschritt_prozent": plan["progress"],
            "intervall_monate": v.interval_months, "intervall_km": v.interval_km,
            "grundlage": "letzte Inspektion + Intervall (berechnet)",
        })
        await ha.set_state(f"{base}_status", plan["status"], {
            "friendly_name": f"{v.name} – Inspektionsstatus",
            "icon": STATUS_ICON[plan["status"]],
            "rueckruf_offen": item.get("recall_open"), "fehler": item.get("error"),
        })
    await ha.set_state("sensor.inspektion_abruf", datetime.now().astimezone().isoformat(timespec="seconds"), {
        "friendly_name": "Inspektionen – letzte Synchronisierung",
        "device_class": "timestamp", "icon": "mdi:cloud-sync",
        "naechster_abruf": next_at.isoformat(timespec="minutes"), "version": __version__,
    })


# ---------------------------------------------------------------- stdin-Befehle
async def stdin_commands(queue: asyncio.Queue[str]) -> None:
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:  # EOF (z. B. lokaler Start ohne stdin)
            return
        line = line.strip()
        try:
            line = json.loads(line) if line.startswith(("\"", "{")) else line
            if isinstance(line, dict):
                line = line.get("command", "")
        except json.JSONDecodeError:
            pass
        await queue.put(str(line).strip())


async def main() -> None:
    settings = load_settings()
    ha = HomeAssistant()
    cache = load_cache()
    log.info("Motorrad-Inspektionen %s – %d Fahrzeug(e), Abruf am %d. jedes Monats um %s",
             __version__, len(settings.vehicles), settings.schedule_day, settings.schedule_time)

    queue: asyncio.Queue[str] = asyncio.Queue()
    asyncio.create_task(stdin_commands(queue))

    stale_before = datetime.now() - timedelta(days=settings.max_age_days)
    stale = [v.id for v in settings.vehicles
             if not cache.get(v.id, {}).get("last_ok")
             or datetime.fromisoformat(cache[v.id]["last_ok"]) < stale_before]
    next_at = next_run(settings, datetime.now())
    await push_states(ha, settings, cache, next_at)
    for vid in stale:
        log.info("%s: keine aktuellen Daten – rufe jetzt ab", vid)
        await refresh(settings, cache, vid)
    if stale:
        await push_states(ha, settings, cache, next_at)

    while True:
        timeout = min(PUSH_EVERY, max(next_at - datetime.now(), timedelta(seconds=1))).total_seconds()
        command = None
        try:
            command = await asyncio.wait_for(queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

        if command:
            parts = command.split()
            if parts[0].lower() == "refresh":
                target = parts[1] if len(parts) > 1 else None
                log.info("Manueller Abruf%s", f" für {target}" if target else "")
                await refresh(settings, cache, target)
            else:
                log.warning("Unbekannter Befehl '%s' (erlaubt: refresh [id])", command)
        elif datetime.now() >= next_at:
            log.info("Monatlicher Abruf")
            await refresh(settings, cache)
            next_at = next_run(settings, datetime.now())
            if any(cache.get(v.id, {}).get("error") for v in settings.vehicles):
                next_at = datetime.now() + timedelta(days=1)  # bei Fehler morgen erneut
                log.info("Fehler beim Abruf – neuer Versuch am %s", next_at.strftime("%d.%m.%Y %H:%M"))

        await push_states(ha, settings, cache, next_at)


if __name__ == "__main__":
    asyncio.run(main())
