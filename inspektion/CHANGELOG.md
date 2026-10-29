# Changelog

## 2.1.1 – 2026-10-29
- Husqvarna-Login: E-Mail-Feld ohne Kennzeichnung (SAP-Gigya-Formular) wird über die Feldreihenfolge erkannt
- Warten, bis das per JavaScript aufgebaute Anmeldeformular geladen ist
- Gigya-Sitzungsdaten werden nie mitgeschnitten

## 2.1.0 – 2026-10-29
- KTM: Abruf direkt über die Servicelookup-Schnittstelle (nur FIN, kein Kennwort, kein Browser)
- Husqvarna: Login über /login/, Motorrad per `bike_id`, Serviceheft-Tabelle und Detailseite des jüngsten Eintrags
- Neue Attribute: modelljahr, rueckruf_hinweis, roadside_assistance_bis, letzter_service_laut_portal
- Kennwort für FIN-Portale optional; Standardintervall 10.000 km
- Falsche Zugangsdaten werden nicht erneut versucht

## 2.0.1 – 2026-10-29
- Basis-Image fest auf python:3.12-slim-bookworm (build.yaml entfernt)

## 2.0.0 – 2026-10-28
- Umbau von eigenständiger Web-App zu Home-Assistant-Add-on
- Husqvarna: neues Serviceheft unter my.husqvarna-motorcycles.com (Login mit E-Mail + Kennwort)
- KTM: Service & Sicherheitscheck (FIN + Kennwort)
- Letzte Inspektion mit allen vom Portal gelieferten Feldern als Sensor-Attribute
- Abruf monatlich, Sofortabruf über `hassio.addon_stdin`
- Nächste Inspektion berechnet aus Intervall (Monate/km) und Kilometerstand-Helfer
