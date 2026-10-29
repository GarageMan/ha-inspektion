"""Unit-Tests mit Daten, wie sie die Portale anzeigen.
Ausführen:  cd inspektion && python -m pytest ../tests -q
"""
from datetime import date, datetime

from app.config import Settings, Vehicle
from app.main import next_run
from app.parser import detail_fields, parse, parse_date, parse_km
from app.planner import add_months, compute_status

KTM_TEXT = """SERVICE & SICHERHEITSCHECK
Gib die 17-stellige Fahrzeugidentifikationsnummer deines Motorrads ein. Dann siehst du den Serviceverlauf deines Motorrads und ob dein Motorrad von einem aktuellen Rückruf oder einer Sicherheitswarnung betroffen ist. In diesem Fall wende dich bitte umgehend an einen autorisierten KTM-Händler
FAHRZEUGIDENTIFIKATIONSNUMMER *
VIN PRÜFEN
690 ENDURO R, MODELLJAHR 2021
Keine Rückrufe oder Sicherheitswarnungen für Ihre VIN
Datum\tLaufleistung\tHändler
22.09.2026\t15.539 km\tMusterhandel GmbH
21.01.2025\t12.633 km\tMusterhandel GmbH
13.12.2023\t10.803 km\tMusterhandel GmbH
01.09.2022\t8.859 km\tMusterhandel GmbH
"""
KTM_TABLE = [["Datum", "Laufleistung", "Händler"],
             ["22.09.2026", "15.539 km", "Musterhandel GmbH"],
             ["21.01.2025", "12.633 km", "Musterhandel GmbH"],
             ["13.12.2023", "10.803 km", "Musterhandel GmbH"],
             ["01.09.2022", "8.859 km", "Musterhandel GmbH"]]

HQV_BIKE_TEXT = """my.husqvarna-motorcycles
Garage
701 Supermoto 2022 EU
Lutz B.
Garage
Verwalte alle deine Motorräder
Besitznachweis
Level 3
Bedienungsanleitungen
Roadside Assistance
Aktiv bis 9.12.2026
Deine Roadside Assistance läuft bald ab, bitte kontaktiere deinen Händler.
Details
Serviceheft
Letztes Service: 9.12.2025
Details
HUSQVARNA
701 Supermoto 2022 EU
Drehmoment
735 NM
"""
# Serviceheft als <div>-Liste (jede Zelle eine Zeile) – Worst Case ohne <table>
HQV_SERVICEBOOK_TEXT = """Serviceheft
Verwalte deine Services oder füge Neue hinzu
Services exportieren
Service hinzufügen
DATUM
DISTANZ
HÄNDLER
QUELLE
NOTIZEN
9.12.2025
15373 km
Musterhandel GmbH
Importiert von Händler
Flexible service
13.12.2024
11031 km
Musterhandel GmbH
Importiert von Händler
Flexible service
13.12.2023
8203 km
Musterhandel GmbH
Importiert von Händler
Flexible service
1.6.2023
6072 km
Musterhandel GmbH
Importiert von Händler
Flexible service
4.5.2022
1101 km
Musterhandel GmbH
Importiert von Händler
Flexible service
"""
HQV_TABLE = [["DATUM", "DISTANZ", "HÄNDLER", "QUELLE", "NOTIZEN", ""],
             ["9.12.2025", "15373 km", "Musterhandel GmbH", "Importiert von Händler", "Flexible service", ""],
             ["13.12.2024", "11031 km", "Musterhandel GmbH", "Importiert von Händler", "Flexible service", ""]]
HQV_DETAIL = {
    "HÄNDLER": "Musterhandel GmbH", "DATUM": "09.12.2025", "KM": "15373",
    "HÄNDLER NOTIZEN": "Flexible service", "NOTIZEN (MAX. 1000 ZEICHEN)": "",
    "_text": "Service bearbeiten\nHÄNDLER\nMusterhandel GmbH\nDATUM\n09.12.2025\nKM\n15373\n"
             "HÄNDLER NOTIZEN\nFlexible service\nNOTIZEN\n(MAX. 1000 ZEICHEN)\n0 / 1000\nSpeichern",
}

VIN = "VBKTV9400RM123456"


def V(**kw):
    base = dict(id="ktm", name="KTM", portal="ktm_service_check", vin=VIN, interval_months=12, interval_km=10000)
    base.update(kw)
    return Vehicle(**base)


def test_basics():
    assert parse_date("9.12.2025") == date(2025, 12, 9)
    assert parse_date("2025-05-20T10:00:00Z") == date(2025, 5, 20)
    assert parse_km("15.539 km") == 15539 and parse_km("15373 km") == 15373
    assert add_months(date(2025, 1, 31), 1) == date(2025, 2, 28)


def test_ktm_table():
    p = parse({"tables": [KTM_TABLE], "text": KTM_TEXT, "json": []})
    last = p["entries"][0]
    assert (last["date"], last["km"], last["dealer"]) == (date(2026, 9, 22), 15539, "Musterhandel GmbH")
    assert len(p["entries"]) == 4
    assert p["model"] == "690 ENDURO R" and p["extras"]["modelljahr"] == 2021
    assert p["recall_open"] is False
    assert p["extras"]["rueckruf_hinweis"].startswith("Keine Rückrufe")
    assert last["details"]["Laufleistung"] == "15.539 km"


def test_ktm_text_only():
    p = parse({"tables": [], "text": KTM_TEXT, "json": []})
    last = p["entries"][0]
    assert (last["date"], last["km"], last["dealer"]) == (date(2026, 9, 22), 15539, "Musterhandel GmbH")
    assert len(p["entries"]) == 4


def test_husqvarna_table_detail_and_bike():
    p = parse({"tables": [HQV_TABLE], "text": "", "detail": HQV_DETAIL, "bike_text": HQV_BIKE_TEXT,
               "json": [], "bike_id": "0123456789abcdef01234567"})
    last = p["entries"][0]
    assert (last["date"], last["km"], last["dealer"]) == (date(2025, 12, 9), 15373, "Musterhandel GmbH")
    assert last["description"] == "Flexible service"
    assert last["details"]["Quelle"] == "Importiert von Händler"
    assert last["details"]["Händler notizen"] == "Flexible service"
    assert "Notizen" not in last["details"] or last["details"]["Notizen"] == "Flexible service"
    assert p["model"] == "701 Supermoto 2022 EU"
    assert p["extras"]["roadside_assistance_bis"] == "2026-12-09"
    assert p["extras"]["letzter_service_laut_portal"] == "2025-12-09"
    assert "läuft bald ab" in p["extras"]["roadside_hinweis"]
    assert p["extras"]["bike_id"] == "0123456789abcdef01234567"


def test_husqvarna_div_list_fallback():
    p = parse({"tables": [], "text": HQV_SERVICEBOOK_TEXT, "json": []})
    assert [e["km"] for e in p["entries"]] == [15373, 11031, 8203, 6072, 1101]
    assert p["entries"][0]["details"]["Quelle"] == "Importiert von Händler"
    assert p["entries"][0]["description"] == "Flexible service"


def test_detail_text_fallback_and_wrong_row():
    f = detail_fields({"_text": HQV_DETAIL["_text"]})
    assert f["Händler"] == "Musterhandel GmbH" and f["Km"] == "15373" and f["Händler notizen"] == "Flexible service"
    assert "Notizen" not in f  # leeres Feld, Zähler "0 / 1000" darf kein Wert sein
    wrong = dict(HQV_DETAIL, DATUM="13.12.2024")
    p = parse({"tables": [HQV_TABLE], "text": "", "detail": wrong, "json": []})
    assert "Händler notizen" not in p["entries"][0]["details"]


def test_planner_with_real_values():
    last = {"date": date(2025, 12, 9), "km": 15373}
    s = compute_status(V(), last, 15373, today=date(2026, 10, 29))
    assert s["next_date"] == date(2026, 12, 9) and s["days_left"] == 41 and s["status"] == "ok"
    assert s["next_km"] == 25373
    assert compute_status(V(), last, 25000, today=date(2026, 10, 29))["status"] == "bald fällig"
    assert compute_status(V(), last, None, today=date(2026, 12, 10))["status"] == "überfällig"


def test_next_run():
    s = Settings(schedule_day=1, schedule_time="06:17", max_age_days=31, debug_dump=False, vehicles=[])
    assert next_run(s, datetime(2026, 10, 29, 11, 0)) == datetime(2026, 11, 1, 6, 17)
    assert next_run(s, datetime(2026, 12, 5, 8, 0)) == datetime(2027, 1, 1, 6, 17)


KTM_LOOKUP = {"status": {"success": True, "messages": ["Keine Rückrufe oder Sicherheitswarnungen für Ihre VIN"]},
              "data": {"modelName": "690 ENDURO R", "modelYear": 2021, "isRecall": False, "services": [
                  {"date": "22.09.2026", "action": "Flexible service", "dealer": "Musterhandel GmbH", "mileage": "15.539 km"},
                  {"date": "21.01.2025", "action": "Flexible service", "dealer": "Musterhandel GmbH", "mileage": "12.633 km"},
                  {"date": "13.12.2023", "action": "Flexible service", "dealer": "Musterhandel GmbH", "mileage": "10.803 km"},
                  {"date": "01.09.2022", "action": "Flexible service", "dealer": "Musterhandel GmbH", "mileage": "8.859 km"},
                  {"date": "03.03.2022", "action": "Flexible service", "dealer": "Musterhandel GmbH", "mileage": "7.158 km"},
                  {"date": "03.03.2021", "action": "Flexible service", "dealer": "Musterhandel GmbH", "mileage": "952 km"}]}}


def test_ktm_servicelookup_json():
    p = parse({"json": [{"url": "x", "status": 200, "body": KTM_LOOKUP}], "tables": [], "text": ""})
    last = p["entries"][0]
    assert (last["date"], last["km"], last["dealer"], last["description"]) == (
        date(2026, 9, 22), 15539, "Musterhandel GmbH", "Flexible service")
    assert last["details"] == {"Datum": "22.09.2026", "Leistung": "Flexible service",
                               "Händler": "Musterhandel GmbH", "Laufleistung": "15.539 km",
                               "_raw_date": "22.09.2026"}
    assert len(p["entries"]) == 6 and p["entries"][-1]["km"] == 952
    assert p["model"] == "690 ENDURO R" and p["extras"]["modelljahr"] == 2021
    assert p["recall_open"] is False and p["login_error"] is None


def test_ktm_api_preferred_over_table():
    p = parse({"json": [{"url": "x", "status": 200, "body": KTM_LOOKUP}], "tables": [KTM_TABLE], "text": KTM_TEXT})
    assert p["entries"][0]["description"] == "Flexible service" and len(p["entries"]) == 6
