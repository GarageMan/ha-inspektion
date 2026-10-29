"""Add-on-Optionen (/data/options.json) und Portal-Definitionen."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

OPTIONS_PATH = Path(os.getenv("OPTIONS_PATH", "/data/options.json"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DEBUG_DIR = Path(os.getenv("DEBUG_DIR", "/share/inspektion-debug"))
SELECTOR_OVERRIDES = Path(os.getenv("SELECTOR_OVERRIDES", "/share/inspektion/selectors.json"))

VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
BIKE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")

# login: "vin"     = Service & Sicherheitscheck: nur FIN (Kennwort optional, falls verlangt)
#        "account" = MY.-Portal (Garage/Serviceheft): E-Mail + Kennwort
PORTALS: dict[str, dict[str, str]] = {
    "ktm_service_check": {
        "brand": "KTM", "login": "vin",
        "url": "https://www.ktm.com/de-de/service/Service-and-safety-check.html",
        # Datenschnittstelle hinter „VIN prüfen“ – liefert JSON, kein Browser nötig
        "api": "https://www.ktm.com/de-de/service/Service-and-safety-check/_jcr_content/root/"
               "responsivegrid_1_col/servicelookup.servicelookup.json?vin={vin}",
    },
    "husqvarna_service_check": {
        "brand": "Husqvarna", "login": "vin",
        "url": "https://www.husqvarna-motorcycles.com/de-de/service/Service-and-safety-check.html",
        "api": "https://www.husqvarna-motorcycles.com/de-de/service/Service-and-safety-check/_jcr_content/root/"
               "responsivegrid_1_col/servicelookup.servicelookup.json?vin={vin}",  # vermutet, analog KTM
    },
    "husqvarna_servicebook": {
        "brand": "Husqvarna", "login": "account",
        "url": "https://my.husqvarna-motorcycles.com/de/de/servicebook/",
        "base": "https://my.husqvarna-motorcycles.com/de/de",
    },
    # Gleiche Plattform – falls das KTM-Motorrad in MY.KTM hinterlegt ist (ungetestet)
    "ktm_servicebook": {
        "brand": "KTM", "login": "account",
        "url": "https://my.ktm.com/de/de/servicebook/",
        "base": "https://my.ktm.com/de/de",
    },
}

SELECTORS: dict[str, list[str]] = {
    "cookie_reject": [
        "#onetrust-reject-all-handler",
        "button:has-text('Alle ablehnen')",
        "button:has-text('Ablehnen')",
        "button:has-text('Nur notwendige')",
        "button:has-text('Nur erforderliche')",
        "#CybotCookiebotDialogBodyButtonDecline",
        "button[data-testid='uc-deny-all-button']",
    ],
    "vin": [
        "input[name*='vin' i]", "input[id*='vin' i]",
        "input[placeholder*='FIN' i]", "input[placeholder*='VIN' i]",
        "input[placeholder*='Fahrzeugidentifikation' i]", "input[aria-label*='Fahrzeugidentifikation' i]",
        "input[maxlength='17']", "main input[type='text']",
    ],
    "vin_submit": [
        "button:has-text('VIN prüfen')", "button:has-text('Prüfen')",
        "form button[type='submit']", "input[type='submit']",
    ],
    "vin_result": [
        "table:has-text('Laufleistung')", "text=Laufleistung",
        "text=Keine Rückrufe", "text=Rückruf", "text=Serviceverlauf",
    ],
    "username": [
        "input[type='email']", "input[name='email' i]", "input[id*='email' i]",
        "input[name='username' i]", "input[autocomplete='username']",
    ],
    "password": ["input[type='password']"],
    "login_submit": [
        "button:has-text('Anmelden')", "form button[type='submit']", "input[type='submit']",
    ],
    "servicebook_ready": [
        "table:has-text('Distanz')", "text=Distanz", "text=Händler", "table", "[role='row']",
    ],
    "detail_ready": ["text=Service bearbeiten", "text=Händler Notizen", "textarea"],
}


@dataclass
class Vehicle:
    id: str
    name: str
    portal: str
    vin: str = ""
    username: str = ""
    password: str = ""
    bike_id: str = ""
    interval_months: int | None = 12
    interval_km: int | None = 10000
    odometer_entity: str = ""
    warn_days: int = 30
    warn_km: int = 1000
    selectors: dict[str, list[str]] = field(default_factory=dict)

    @property
    def brand(self) -> str:
        return PORTALS[self.portal]["brand"]

    @property
    def url(self) -> str:
        return PORTALS[self.portal]["url"]

    @property
    def api(self) -> str:
        return PORTALS[self.portal].get("api", "")

    @property
    def base(self) -> str:
        return PORTALS[self.portal].get("base", "")

    @property
    def login(self) -> str:
        return PORTALS[self.portal]["login"]

    @property
    def vin_masked(self) -> str:
        return f"{self.vin[:3]}…{self.vin[-4:]}" if self.vin else ""


@dataclass
class Settings:
    schedule_day: int
    schedule_time: str
    max_age_days: int
    debug_dump: bool
    vehicles: list[Vehicle]


def _selectors_for(vid: str) -> dict[str, list[str]]:
    sel = {k: list(v) for k, v in SELECTORS.items()}
    if SELECTOR_OVERRIDES.is_file():
        over = json.loads(SELECTOR_OVERRIDES.read_text(encoding="utf-8"))
        for key, value in {**over.get("*", {}), **over.get(vid, {})}.items():
            sel[key] = [value] if isinstance(value, str) else list(value)
    return sel


def load_settings(path: Path = OPTIONS_PATH) -> Settings:
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    vehicles: list[Vehicle] = []
    for item in raw.get("vehicles", []):
        vid = item["id"]
        portal = item["portal"]
        if portal not in PORTALS:
            raise ValueError(f"{vid}: unbekanntes Portal '{portal}'")
        login = PORTALS[portal]["login"]
        vin = (item.get("vin") or "").strip().upper()
        user = (item.get("username") or "").strip()
        password = item.get("password") or ""
        bike_id = (item.get("bike_id") or "").strip()
        if vin and not VIN_RE.match(vin):
            raise ValueError(f"{vid}: FIN ist ungültig (17 Zeichen, ohne I/O/Q).")
        if login == "vin" and not vin:
            raise ValueError(f"{vid}: Portal '{portal}' braucht die 17-stellige FIN (vin).")
        if login == "account" and not (user and password):
            raise ValueError(f"{vid}: Portal '{portal}' braucht username (E-Mail) und password.")
        if bike_id and not BIKE_ID_RE.match(bike_id):
            raise ValueError(f"{vid}: bike_id ist ungültig – nur den Wert hinter 'bikeId=' eintragen.")
        vehicles.append(Vehicle(
            id=vid, name=item.get("name") or vid, portal=portal,
            vin=vin, username=user, password=password, bike_id=bike_id,
            interval_months=item.get("interval_months") or None,
            interval_km=item.get("interval_km") or None,
            odometer_entity=(item.get("odometer_entity") or "").strip(),
            warn_days=int(item.get("warn_days") or 30),
            warn_km=int(item.get("warn_km") or 1000),
            selectors=_selectors_for(vid),
        ))
    if not vehicles:
        raise ValueError("Keine Fahrzeuge konfiguriert.")
    if len({v.id for v in vehicles}) != len(vehicles):
        raise ValueError("Fahrzeug-IDs müssen eindeutig sein.")
    return Settings(
        schedule_day=int(raw.get("schedule_day", 1)),
        schedule_time=str(raw.get("schedule_time", "06:17")),
        max_age_days=int(raw.get("max_age_days", 31)),
        debug_dump=bool(raw.get("debug_dump", False)),
        vehicles=vehicles,
    )
