"""Portal-Abruf per Headless-Chromium (Playwright).

KTM Service & Sicherheitscheck (login = "vin"):
    Seite öffnen → FIN eintragen → „VIN prüfen“ → Modell, Rückrufhinweis und
    Tabelle Datum | Laufleistung | Händler auslesen.

MY.-Portal Husqvarna/KTM (login = "account"):
    /login/ → E-Mail + Kennwort → „Anmelden“
    → Motorradseite /garage/bike/?bikeId=…   (Modell, Roadside Assistance, letzter Service)
    → Serviceheft /servicebook/?bikeId=…     (Tabelle Datum | Distanz | Händler | Quelle | Notizen)
    → jüngsten Eintrag anklicken → „Service bearbeiten“ (alle Detailfelder, nur lesen!)

Es wird ausschließlich gelesen – auf der Detailseite wird nichts gespeichert.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

from playwright.async_api import Locator, Page, TimeoutError as PwTimeout, async_playwright

from .config import DEBUG_DIR, Vehicle

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)
BIKE_ID_IN_URL = re.compile(r"[?&]bikeId=([A-Za-z0-9_-]+)")

# Alle Tabellen (HTML-<table> und ARIA-Grids) als Liste von Zeilen/Zellen
JS_TABLES = """
() => {
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
  const out = [];
  document.querySelectorAll('table').forEach(t => {
    const rows = [...t.querySelectorAll('tr')]
      .map(r => [...r.querySelectorAll('th,td')].map(c => clean(c.innerText)))
      .filter(r => r.some(c => c));
    if (rows.length > 1) out.push(rows);
  });
  document.querySelectorAll('[role=table],[role=grid]').forEach(t => {
    const rows = [...t.querySelectorAll('[role=row]')]
      .map(r => [...r.querySelectorAll('[role=columnheader],[role=cell],[role=gridcell],[role=rowheader]')]
        .map(c => clean(c.innerText)))
      .filter(r => r.some(c => c));
    if (rows.length > 1) out.push(rows);
  });
  return out;
}
"""

# Formularfelder der Detailseite als {Beschriftung: Wert}
JS_FIELDS = """
() => {
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
  const isCounter = t => /^\\d+\\s*\\/\\s*\\d+$/.test(t);
  const out = {};
  const els = [...document.querySelectorAll('input,textarea,select')]
    .filter(e => !['hidden', 'password', 'search', 'checkbox', 'radio', 'submit', 'button'].includes(e.type))
    .filter(e => e.offsetParent !== null);
  for (const el of els) {
    let label = el.labels && el.labels[0] ? clean(el.labels[0].innerText) : '';
    if (!label) label = clean(el.getAttribute('aria-label') || '');
    let node = el;
    for (let i = 0; i < 4 && node && !label; i++) {
      let prev = node.previousElementSibling;
      while (prev && !label) {
        const t = clean(prev.innerText);
        if (t && t.length < 60 && !isCounter(t)) label = t;
        prev = prev.previousElementSibling;
      }
      node = node.parentElement;
    }
    if (!label) label = clean(el.placeholder || el.name || el.id || '');
    if (label && !(label in out)) out[label] = clean(el.value);
  }
  return out;
}
"""

VISIBLE_INPUTS = (
    "input:not([type='hidden']):not([type='checkbox']):not([type='radio'])"
    ":not([type='submit']):not([type='button']):not([type='search']):visible"
)

JS_BIKE_LINKS = """
() => [...document.querySelectorAll('a[href*="bikeId="]')].map(a => a.href)
"""


class ScrapeError(RuntimeError):
    pass


async def _first_visible(page: Page, selectors: list[str], timeout_ms: int = 15000) -> Locator | None:
    """Erster sichtbarer Treffer in Hauptseite und allen iFrames."""
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        for frame in page.frames:
            for sel in selectors:
                try:
                    loc = frame.locator(sel).first
                    if await loc.count() and await loc.is_visible():
                        return loc
                except Exception:  # noqa: BLE001
                    continue
        if time.monotonic() >= deadline:
            return None
        await page.wait_for_timeout(500)


async def _click_if_present(page: Page, selectors: list[str], timeout_ms: int) -> bool:
    loc = await _first_visible(page, selectors, timeout_ms)
    if not loc:
        return False
    try:
        await loc.click(timeout=10000)  # wartet auch, bis der Button aktiviert ist
        return True
    except Exception:  # noqa: BLE001
        return False


async def _settle(page: Page, extra_ms: int = 2000) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=20000)
    except PwTimeout:
        pass
    await page.wait_for_timeout(extra_ms)


async def _page_text(page: Page) -> str:
    text = ""
    for frame in page.frames:
        try:
            text += "\n" + await frame.inner_text("body")
        except Exception:  # noqa: BLE001
            continue
    return text


class _Run:
    """Kontext eines Abrufs: Seite, mitgeschnittene JSON-Antworten, Debug-Dumps."""

    def __init__(self, v: Vehicle, page: Page, debug: bool) -> None:
        self.v, self.page, self.debug = v, page, debug
        self.stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.json: list[dict[str, Any]] = []
        self.step = 0

    async def on_response(self, resp) -> None:
        try:
            if resp.request.resource_type not in ("xhr", "fetch"):
                return
            if "json" not in (resp.headers.get("content-type") or ""):
                return
            body = await resp.json()
            if isinstance(body, dict) and any(k in body for k in (
                    "access_token", "id_token", "refresh_token", "token",
                    "sessionInfo", "login_token", "sessionToken", "sessionSecret", "UID", "UIDSignature")):
                return  # Login-/Sitzungsdaten (u. a. Gigya) nie aufheben
            self.json.append({"url": resp.url.split("?")[0], "status": resp.status, "body": body})
        except Exception:  # noqa: BLE001
            return

    async def snap(self, name: str) -> None:
        if not self.debug:
            return
        self.step += 1
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        prefix = DEBUG_DIR / f"{self.v.id}-{self.stamp}-{self.step:02d}-{name}"
        try:
            await self.page.screenshot(path=f"{prefix}.png", full_page=True)
            (prefix.with_suffix(".txt")).write_text(await _page_text(self.page), encoding="utf-8")
            log.info("Debug: %s.png", prefix)
        except Exception as exc:  # noqa: BLE001
            log.info("Debug-Screenshot fehlgeschlagen: %s", exc)

    def dump_json(self) -> None:
        if self.debug:
            DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            path = DEBUG_DIR / f"{self.v.id}-{self.stamp}-responses.json"
            path.write_text(json.dumps(self.json, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


# --------------------------------------------------------------------------- KTM / FIN
async def _flow_vin(run: _Run) -> dict[str, Any]:
    page, v, sel = run.page, run.v, run.v.selectors
    await page.goto(v.url, wait_until="domcontentloaded", timeout=60000)
    await _click_if_present(page, sel["cookie_reject"], 6000)

    vin_input = await _first_visible(page, sel["vin"], 20000)
    if not vin_input:
        await run.snap("fin-feld-fehlt")
        raise ScrapeError("FIN-Feld nicht gefunden.")
    await vin_input.fill(v.vin)

    # Kennwort nur, falls das Portal tatsächlich eines verlangt
    pw = await _first_visible(page, sel["password"], 1500)
    if pw and v.password:
        await pw.fill(v.password)

    if not await _click_if_present(page, sel["vin_submit"], 5000):
        await vin_input.press("Enter")

    if not await _first_visible(page, sel["vin_result"], 30000):
        await run.snap("kein-ergebnis")
        hint = (await _page_text(page)).lower()
        if "ungültig" in hint or "nicht gefunden" in hint or "invalid" in hint:
            raise ScrapeError("FIN wurde vom Portal nicht akzeptiert – FIN prüfen.")
        raise ScrapeError("Nach „VIN prüfen“ erschien keine Servicetabelle.")
    await _settle(page, 1500)
    await run.snap("ergebnis")

    return {"tables": await page.evaluate(JS_TABLES), "text": await _page_text(page),
            "detail": {}, "bike_text": ""}


# --------------------------------------------------------------------- MY.-Portal / Konto
async def _login(run: _Run) -> None:
    page, v, sel = run.page, run.v, run.v.selectors
    await page.goto(f"{v.base}/login/", wait_until="domcontentloaded", timeout=60000)
    await _click_if_present(page, sel["cookie_reject"], 6000)

    if "/login" not in page.url:
        log.info("%s: bereits angemeldet", v.id)
        return
    # Formular wird per JavaScript (SAP Gigya) aufgebaut – erst warten, bis Eingabefelder da sind
    fields = page.locator(VISIBLE_INPUTS)
    try:
        await fields.first.wait_for(state="visible", timeout=20000)
    except PwTimeout:
        await run.snap("login-felder-fehlen")
        raise ScrapeError("Anmeldeformular nicht geladen.") from None

    user = await _first_visible(page, sel["username"], 2000)
    pw = await _first_visible(page, sel["password"], 2000)
    if not user or not pw:
        # Rückfallebene: Die Anmeldeseite hat genau zwei Felder – 1. E-Mail, 2. Passwort.
        # (Das E-Mail-Feld ist dort ein unbenanntes <input type="text" class="input-field">.)
        count = await fields.count()
        if count >= 2:
            user = user or fields.nth(0)
            pw = pw or fields.nth(1)
            log.info("%s: Anmeldefelder über ihre Reihenfolge erkannt (%d Felder)", v.id, count)
    if not user or not pw:
        await run.snap("login-felder-fehlen")
        raise ScrapeError("Anmeldefelder (E-Mail/Passwort) nicht gefunden.")
    await user.fill(v.username)
    await pw.fill(v.password)
    await pw.press("Tab")  # Formularprüfung auslösen → „Anmelden“ wird aktiv

    if not await _click_if_present(page, sel["login_submit"], 5000):
        await pw.press("Enter")
    try:
        await page.wait_for_url(lambda u: "/login" not in u, timeout=45000)
    except PwTimeout:
        try:
            await pw.fill("")  # Kennwort nicht im Screenshot
        except Exception:  # noqa: BLE001
            pass
        await run.snap("login-fehlgeschlagen")
        raise ScrapeError("Login fehlgeschlagen – E-Mail/Passwort prüfen.") from None
    await _settle(page)
    log.info("%s: angemeldet", v.id)


async def _find_bike_id(run: _Run) -> str:
    page, v = run.page, run.v
    if v.bike_id:
        return v.bike_id
    for attempt in (page.url, f"{v.base}/garage/"):
        if attempt != page.url:
            await page.goto(attempt, wait_until="domcontentloaded", timeout=60000)
            await _settle(page)
        m = BIKE_ID_IN_URL.search(page.url)
        if m:
            return m[1]
        for href in await page.evaluate(JS_BIKE_LINKS):
            m = BIKE_ID_IN_URL.search(href)
            if m:
                log.info("%s: bikeId automatisch ermittelt: %s (Tipp: als bike_id eintragen)", v.id, m[1])
                return m[1]
    await run.snap("bikeid-fehlt")
    raise ScrapeError("bikeId nicht gefunden – bitte bike_id in der Konfiguration eintragen.")


async def _open_latest_detail(run: _Run, tables: list[list[list[str]]]) -> dict[str, str]:
    """Jüngsten Serviceeintrag anklicken und alle Felder der Detailseite lesen."""
    from .parser import entries_from_tables  # Zirkelimport vermeiden

    page, sel = run.page, run.v.selectors
    entries = entries_from_tables(tables)
    if not entries:
        return {}
    raw_date = entries[0]["details"].get("_raw_date") or entries[0]["date"].strftime("%-d.%-m.%Y")
    target = page.locator("tr", has_text=raw_date).first
    if not await target.count():
        target = page.get_by_text(raw_date, exact=True).first
    if not await target.count():
        log.info("Detailansicht: Eintrag %s nicht anklickbar", raw_date)
        return {}
    try:
        await target.click(timeout=10000)
    except Exception as exc:  # noqa: BLE001
        log.info("Detailansicht nicht geöffnet: %s", exc)
        return {}
    if not await _first_visible(page, sel["detail_ready"], 20000):
        log.info("Detailansicht nicht erkannt")
        return {}
    await _settle(page, 1500)
    await run.snap("service-details")
    fields: dict[str, str] = await page.evaluate(JS_FIELDS)
    fields["_text"] = await _page_text(page)
    return fields


async def _flow_account(run: _Run) -> dict[str, Any]:
    page, v, sel = run.page, run.v, run.v.selectors
    await _login(run)
    bike_id = await _find_bike_id(run)

    await page.goto(f"{v.base}/garage/bike/?bikeId={bike_id}&source=overview",
                    wait_until="domcontentloaded", timeout=60000)
    await _settle(page)
    await run.snap("motorrad")
    bike_text = await _page_text(page)

    await page.goto(f"{v.base}/servicebook/?bikeId={bike_id}", wait_until="domcontentloaded", timeout=60000)
    if not await _first_visible(page, sel["servicebook_ready"], 30000):
        await run.snap("serviceheft-leer")
        raise ScrapeError("Serviceheft nicht geladen.")
    await _settle(page)
    await run.snap("serviceheft")
    tables = await page.evaluate(JS_TABLES)
    text = await _page_text(page)

    detail = await _open_latest_detail(run, tables)
    return {"tables": tables, "text": text, "detail": detail, "bike_text": bike_text, "bike_id": bike_id}


# --------------------------------------------------------------- KTM: direkte Schnittstelle
def _fetch_api_sync(v: Vehicle) -> dict[str, Any]:
    url = v.api.format(vin=urllib.parse.quote(v.vin))
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "application/json, text/plain, */*",
        "Accept-Language": "de-DE,de;q=0.9", "Referer": v.url, "X-Requested-With": "XMLHttpRequest",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def _fetch_api(v: Vehicle, debug: bool) -> dict[str, Any] | None:
    """Servicelookup-JSON direkt abrufen. None → Browser-Abruf als Rückfallebene."""
    if not v.api:
        return None
    try:
        body = await asyncio.to_thread(_fetch_api_sync, v)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        log.info("%s: Direktabruf nicht möglich (%s) – nutze Browser", v.id, exc)
        return None
    if not (isinstance(body, dict) and isinstance(body.get("data"), dict) and "services" in body["data"]):
        log.info("%s: Direktabruf lieferte unerwartete Daten – nutze Browser", v.id)
        return None
    if debug:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        path = DEBUG_DIR / f"{v.id}-{datetime.now():%Y%m%d-%H%M%S}-servicelookup.json"
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Debug: %s", path)
    log.info("%s: Daten direkt über die Portal-Schnittstelle abgerufen", v.id)
    return {"json": [{"url": v.api.split("?")[0], "status": 200, "body": body}],
            "tables": [], "text": "", "detail": {}, "bike_text": "", "url": v.url}


# ------------------------------------------------------------------------------ Einstieg
async def fetch_vehicle(v: Vehicle, debug: bool = False) -> dict[str, Any]:
    """Liefert Rohdaten für parser.parse() oder wirft ScrapeError."""
    if v.login == "vin":
        direct = await _fetch_api(v, debug)
        if direct:
            return direct
    async with async_playwright() as pwr:
        browser = await pwr.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(
            locale="de-DE", timezone_id="Europe/Berlin", user_agent=USER_AGENT,
            viewport={"width": 1440, "height": 1000},
        )
        page = await context.new_page()
        run = _Run(v, page, debug)
        page.on("response", run.on_response)
        try:
            raw = await (_flow_vin(run) if v.login == "vin" else _flow_account(run))
            raw["json"] = run.json
            raw["url"] = page.url.split("?")[0]
            return raw
        except PwTimeout as exc:
            await run.snap("zeitueberschreitung")
            raise ScrapeError(f"Zeitüberschreitung: {exc.message.splitlines()[0]}") from exc
        finally:
            run.dump_json()
            await context.close()
            await browser.close()
