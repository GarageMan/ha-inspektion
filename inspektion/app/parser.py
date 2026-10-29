"""Inspektionen aus den Portaldaten extrahieren.

Reihenfolge der Quellen (zuverlässigste zuerst):
  1. Tabellen der Seite (KTM: Datum | Laufleistung | Händler,
     Husqvarna-Serviceheft: Datum | Distanz | Händler | Quelle | Notizen)
  2. Seitentext mit erkannter Kopfzeile (falls die Tabelle aus <div>s besteht)
  3. Mitgeschnittene JSON-Antworten (nur als Notlösung)
Der jüngste Eintrag wird mit den Feldern der Detailseite („Service bearbeiten“) ergänzt.
JSON-Inhalte werden bewusst NICHT in die Details übernommen (können Halterdaten enthalten).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Iterator

DATE_DE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
DATE_FULL = re.compile(r"^\s*\d{1,2}\.\d{1,2}\.\d{4}\s*$")
DATE_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
KM_TEXT = re.compile(r"(\d{1,3}(?:[.   ]\d{3})+|\d+)\s*km\b", re.IGNORECASE)
COUNTER = re.compile(r"^\d+\s*/\s*\d+$")
MODEL_YEAR = re.compile(r"^\s*(.+?),\s*MODELLJAHR\s+(\d{4})\s*$", re.IGNORECASE | re.MULTILINE)
INTRO_NOISE = re.compile(r"Gib die 17-stellige.*?Händler", re.DOTALL | re.IGNORECASE)

# Reihenfolge wichtig: "Händler Notizen" → notes, "Laufleistung" → km (nicht description)
HEADER_MAP: list[tuple[str, tuple[str, ...]]] = [
    ("notes", ("notiz", "bemerkung", "notes")),
    ("date", ("datum", "date")),
    ("km", ("laufleistung", "distanz", "kilometer", "mileage", "km")),
    ("dealer", ("händler", "haendler", "dealer", "werkstatt")),
    ("source", ("quelle", "source")),
    ("description", ("leistung", "service", "art", "typ")),
]
DETAIL_LABELS = ("HÄNDLER NOTIZEN", "HÄNDLER", "DATUM", "KM", "NOTIZEN", "LAUFLEISTUNG", "DISTANZ")

NEGATIVE_RECALL = ("keine rückruf", "keine offenen", "nicht betroffen", "keine sicherheitswarnung",
                   "no open", "not affected", "no recall")
POSITIVE_RECALL = ("von einem rückruf betroffen", "offener rückruf", "offene rückruf",
                   "rückrufaktion", "bitte wende dich umgehend")


# ------------------------------------------------------------------ Grundfunktionen
def parse_date(value: Any) -> date | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        ts = value / 1000 if value > 10**11 else value
        if 946684800 <= ts <= 4102444800:
            return datetime.fromtimestamp(ts, tz=timezone.utc).date()
        return None
    s = str(value).strip()
    m = DATE_ISO.match(s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = DATE_DE.search(s)
    if m and len(s) <= 25:
        try:
            return date(int(m[3]), int(m[2]), int(m[1]))
        except ValueError:
            return None
    return None


def parse_km(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if 0 <= value < 1_000_000 else None
    s = str(value)
    if len(s) > 20:
        return None
    digits = re.sub(r"[^\d]", "", s)
    return int(digits) if digits and len(digits) <= 6 else None


def map_header(h: str) -> str | None:
    hl = h.lower().strip()
    if not hl or len(hl) > 30:
        return None
    for key, words in HEADER_MAP:
        if any(w == hl or (len(w) > 2 and w in hl) for w in words):
            return key
    return None


def nice_label(label: str) -> str:
    label = re.sub(r"\(.*?\)", "", label).strip(" :*")
    return label[:1].upper() + label[1:].lower() if label.isupper() else label


def _entry(cells: dict[str, str], labels: list[str], values: list[str], source: str) -> dict[str, Any] | None:
    when = parse_date(cells.get("date"))
    if not when:
        return None
    details = {nice_label(l): v for l, v in zip(labels, values) if l and v}
    details["_raw_date"] = (cells.get("date") or "").strip()
    return {
        "date": when,
        "km": parse_km(cells.get("km")),
        "dealer": cells.get("dealer") or None,
        "description": cells.get("notes") or cells.get("description") or "Service",
        "source": source,
        "details": details,
    }


# ------------------------------------------------------------------ 1. Tabellen
def entries_from_tables(tables: list[list[list[str]]]) -> list[dict[str, Any]]:
    entries = []
    for table in tables or []:
        if len(table) < 2:
            continue
        header = table[0]
        keys = [map_header(h) for h in header]
        if "date" not in keys or not ({"km", "dealer"} & set(keys)):
            continue
        for row in table[1:]:
            cells: dict[str, str] = {}
            for i, k in enumerate(keys):
                if k and i < len(row) and k not in cells:
                    cells[k] = row[i]
            e = _entry(cells, header, row, "tabelle")
            if e:
                entries.append(e)
    return entries


# ------------------------------------------------------------------ 2. Text
def _tokens(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        for part in re.split(r"\t| {3,}", line):
            part = part.strip()
            if part and part not in ("→", "›", ">"):
                out.append(part)
    return out


def entries_from_text(text: str) -> list[dict[str, Any]]:
    tokens = _tokens(INTRO_NOISE.sub("", text or ""))
    # Kopfzeile suchen: ≥ 2 aufeinanderfolgende Spaltennamen, beginnend mit "Datum"
    header: list[str] = []
    start = -1
    for i, tok in enumerate(tokens):
        if map_header(tok) == "date":
            j = i + 1
            while j < len(tokens) and map_header(tokens[j]) and map_header(tokens[j]) != "date":
                j += 1
            if j - i >= 2:
                header, start = tokens[i:j], j
                break
    entries: list[dict[str, Any]] = []
    if header:
        keys = [map_header(h) for h in header]
        i = start
        while i < len(tokens):
            if not DATE_FULL.match(tokens[i]):
                i += 1
                continue
            row = [tokens[i]]
            k = i + 1
            while k < len(tokens) and len(row) < len(header) and not DATE_FULL.match(tokens[k]):
                row.append(tokens[k])
                k += 1
            cells = {key: row[n] for n, key in enumerate(keys) if key and n < len(row)}
            e = _entry(cells, header, row, "text")
            if e:
                entries.append(e)
            i = k
    if entries:
        return entries
    # Notlösung: eine Zeile mit Datum und km
    for line in (text or "").splitlines():
        dm, kmm = DATE_DE.search(line), KM_TEXT.search(line)
        if dm and kmm and len(line) < 300:
            when = parse_date(dm[0])
            if when:
                rest = DATE_DE.sub("", KM_TEXT.sub("", line)).strip(" -|:;,\t")
                entries.append({"date": when, "km": parse_km(kmm[1]), "dealer": rest or None,
                                "description": "Service", "source": "text",
                                "details": {"Zeile": line.strip(), "_raw_date": dm[0]}})
    return entries


# ------------------------------------------------------------------ 3. JSON (Notlösung)
def _walk(obj: Any) -> Iterator[dict[str, Any]]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def entries_from_json(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries = []
    for p in payloads:
        for d in _walk(p.get("body")):
            cells: dict[str, str] = {}
            for k, v in d.items():
                key = map_header(re.sub(r"([a-z])([A-Z])", r"\1 \2", k))
                if key and key not in cells and isinstance(v, (str, int, float)) and not isinstance(v, bool):
                    cells[key] = str(v)
            if "date" in cells and "km" in cells and parse_km(cells["km"]) is not None:
                e = _entry(cells, [], [], "api")
                if e:
                    e["details"] = {"Datum": cells["date"], "Km": cells["km"], "_raw_date": cells["date"]}
                    entries.append(e)
    return entries


# ------------------------------------------------------------------ Detailseite
def detail_fields(detail: dict[str, str]) -> dict[str, str]:
    """Formularfelder + Text-Fallback der Seite „Service bearbeiten“ → {Label: Wert}."""
    out: dict[str, str] = {}
    for label, value in (detail or {}).items():
        if label.startswith("_") or not value:
            continue
        out[nice_label(label)] = value
    lines = [l.strip() for l in (detail or {}).get("_text", "").splitlines() if l.strip()]
    labels_upper = set(DETAIL_LABELS)
    for i, line in enumerate(lines):
        lab = re.sub(r"\(.*?\)", "", line).strip().upper()
        if lab not in labels_upper or nice_label(lab) in out:
            continue
        for nxt in lines[i + 1:i + 3]:
            n_up = re.sub(r"\(.*?\)", "", nxt).strip().upper()
            if not n_up or COUNTER.match(nxt) or nxt.startswith("("):
                continue
            if n_up in labels_upper or nxt.lower() in ("speichern", "abbrechen"):
                break
            out[nice_label(lab)] = nxt
            break
    return out


def merge_detail(entry: dict[str, Any], detail: dict[str, str]) -> bool:
    fields = detail_fields(detail)
    if not fields:
        return False
    d = parse_date(fields.get("Datum"))
    if d and d != entry["date"]:
        return False  # falscher Eintrag geöffnet – nichts übernehmen
    for label, value in fields.items():
        entry["details"][label] = value
    notes = fields.get("Händler notizen")
    if notes and entry["description"] in ("Service", None):
        entry["description"] = notes
    if entry["km"] is None:
        entry["km"] = parse_km(fields.get("Km"))
    return True


# ------------------------------------------------------------------ Modell, Rückruf, Extras
def detect_model(text: str, bike_text: str) -> tuple[str | None, int | None]:
    m = MODEL_YEAR.search(text or "")
    if m:
        return m[1].strip(), int(m[2])
    lines = [l.strip() for l in (bike_text or "").splitlines() if l.strip()]
    for i, line in enumerate(lines[:-1]):
        if line.upper() in ("HUSQVARNA", "KTM", "GASGAS"):
            cand = lines[i + 1]
            if 3 <= len(cand) <= 60 and not cand.lower().startswith(("drehmoment", "leistung")):
                return cand, None
    return None, None


def detect_recall(text: str) -> tuple[bool | None, str | None]:
    clean = INTRO_NOISE.sub("", text or "")
    hint = next((l.strip() for l in clean.splitlines()
                 if ("rückruf" in l.lower() or "sicherheitswarnung" in l.lower()) and len(l) < 200), None)
    low = clean.lower()
    if any(p in low for p in NEGATIVE_RECALL):
        return False, hint
    if any(p in low for p in POSITIVE_RECALL):
        return True, hint
    return None, hint


def bike_extras(bike_text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    lines = [l.strip() for l in (bike_text or "").splitlines() if l.strip()]
    for i, line in enumerate(lines):
        low = line.lower()
        if low.startswith("roadside assistance") or "aktiv bis" in low:
            m = re.search(r"aktiv bis\s*(\d{1,2}\.\d{1,2}\.\d{4})", " ".join(lines[i:i + 3]), re.I)
            if m and "roadside_assistance_bis" not in out:
                out["roadside_assistance_bis"] = parse_date(m[1]).isoformat()
        if "läuft bald ab" in low and "roadside_hinweis" not in out:
            out["roadside_hinweis"] = line
        if low.startswith("letztes service") or low.startswith("letzter service"):
            d = parse_date(line.split(":", 1)[-1])
            if d:
                out["letzter_service_laut_portal"] = d.isoformat()
    return out


def detect_login_failure(text: str) -> str | None:
    low = (text or "").lower()
    for marker in ("falsches passwort", "falsches kennwort", "ungültige e-mail", "ungültige anmelde",
                   "anmeldung fehlgeschlagen", "incorrect", "invalid credentials"):
        if marker in low:
            return "Login fehlgeschlagen – Anmeldedaten prüfen."
    return None


def _dedupe(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for e in sorted(entries, key=lambda e: (e["date"], e["km"] or 0, len(e["details"])), reverse=True):
        key = (e["date"], e["km"])
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


# ------------------------------------------------------------------ KTM-Servicelookup (JSON)
def find_servicelookup(payloads: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Antwort von …/servicelookup.servicelookup.json?vin=… erkennen."""
    for p in payloads or []:
        body = p.get("body")
        if isinstance(body, dict) and isinstance(body.get("data"), dict) and "services" in body["data"]:
            return body
    return None


def parse_servicelookup(body: dict[str, Any]) -> dict[str, Any]:
    data = body.get("data") or {}
    messages = (body.get("status") or {}).get("messages") or []
    entries = []
    for s in data.get("services") or []:
        when = parse_date(s.get("date"))
        if not when:
            continue
        details = {nice_label(k[:1].upper() + k[1:]): v for k, v in s.items() if v not in (None, "")}
        details = {{"Action": "Leistung", "Mileage": "Laufleistung", "Dealer": "Händler", "Date": "Datum"}.get(k, k): v
                   for k, v in details.items()}
        details["_raw_date"] = str(s.get("date"))
        entries.append({"date": when, "km": parse_km(s.get("mileage")), "dealer": s.get("dealer") or None,
                        "description": s.get("action") or "Service", "source": "api", "details": details})
    extras: dict[str, Any] = {}
    if data.get("modelYear"):
        extras["modelljahr"] = data["modelYear"]
    if messages:
        extras["rueckruf_hinweis"] = " ".join(str(m) for m in messages)[:300]
    recall = data.get("isRecall")
    return {
        "entries": _dedupe(entries),
        "model": data.get("modelName"),
        "recall_open": recall if isinstance(recall, bool) else None,
        "extras": extras,
        "login_error": None if entries or (body.get("status") or {}).get("success") else
        ("Portal meldet: " + " ".join(str(m) for m in messages))[:200] if messages else None,
        "json_count": 1,
    }


# ------------------------------------------------------------------ Einstieg
def parse(raw: dict[str, Any], vin: str = "") -> dict[str, Any]:  # noqa: ARG001 – vin für spätere Nutzung
    lookup = find_servicelookup(raw.get("json", []))
    if lookup:
        return parse_servicelookup(lookup)
    text = raw.get("text", "")
    bike_text = raw.get("bike_text", "")
    entries = (entries_from_tables(raw.get("tables", []))
               or entries_from_text(text)
               or entries_from_json(raw.get("json", [])))
    entries = _dedupe(entries)
    if entries and raw.get("detail"):
        merge_detail(entries[0], raw["detail"])
    model, model_year = detect_model(text, bike_text)
    recall, recall_hint = detect_recall(text)
    extras = bike_extras(bike_text)
    if model_year:
        extras["modelljahr"] = model_year
    if recall_hint:
        extras["rueckruf_hinweis"] = recall_hint
    if raw.get("bike_id"):
        extras["bike_id"] = raw["bike_id"]
    return {
        "entries": entries,
        "model": model,
        "recall_open": recall,
        "extras": extras,
        "login_error": None if entries else detect_login_failure(text),
        "json_count": len(raw.get("json", [])),
    }
