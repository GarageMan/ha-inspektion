# Motorrad-Inspektionen

Liest **einmal im Monat** die letzte Inspektion deiner Motorräder aus den Herstellerportalen
und stellt sie in Home Assistant als Sensoren bereit – inklusive **aller Felder**, die das
Portal zur Inspektion liefert. Daraus wird zusätzlich der nächste Termin (Datum und km)
berechnet.

| Portal (`portal`) | Login | Seite |
|---|---|---|
| `ktm_service_check` | nur FIN | ktm.com → Service & Sicherheitscheck (direkte Datenschnittstelle, kein Browser) |
| `husqvarna_servicebook` | E-Mail + Kennwort | my.husqvarna-motorcycles.com → Serviceheft |
| `ktm_servicebook` | E-Mail + Kennwort | my.ktm.com → Serviceheft (gleiche Plattform, ungetestet) |
| `husqvarna_service_check` | nur FIN | Husqvarna-Service-Check auf der Hauptseite |

## Konfiguration

```yaml
schedule_day: 1          # Abruf am 1. jedes Monats …
schedule_time: "06:17"   # … um 06:17 Uhr
max_age_days: 31         # beim Start sofort abrufen, wenn Daten älter
debug_dump: false        # true = Debug-Dateien nach /share/inspektion-debug
vehicles:
  - id: ktm
    name: KTM 690 Enduro R
    portal: ktm_service_check
    vin: VBKXXXXXXXXXXXXXX     # Kennwort wird nicht benötigt
    interval_months: 12
    interval_km: 10000
    odometer_entity: input_number.ktm_kilometerstand
  - id: husqvarna
    name: Husqvarna 701 Supermoto
    portal: husqvarna_servicebook
    username: deine@email.de
    password: "…"
    bike_id: 0123456789abcdef01234567   # Wert hinter "bikeId=" in der Adresszeile der Motorradseite
    interval_months: 12
    interval_km: 10000
    odometer_entity: input_number.husqvarna_kilometerstand
```

Optional je Fahrzeug: `warn_days` (Standard 30) und `warn_km` (Standard 1000) für „bald fällig“.

## Ablauf beim Abruf

- **KTM:** `…/servicelookup.servicelookup.json?vin=<FIN>` direkt abfragen (Modell, Modelljahr,
  Rückrufstatus, alle Services mit Datum, Leistung, Händler, Laufleistung). Nur falls das
  scheitert, wird die Seite im Browser bedient („VIN prüfen“).
- **Husqvarna:** Login → Motorradseite (Modell, Roadside Assistance) → Serviceheft
  (Datum, Distanz, Händler, Quelle, Notizen) → jüngsten Eintrag öffnen („Service bearbeiten“,
  nur lesen – es wird nichts gespeichert). Ohne `bike_id` wird das Motorrad automatisch gesucht.
- Falsche Zugangsdaten werden **nicht** wiederholt (Schutz vor Kontosperre).

## Sensoren (je Fahrzeug `<id>`)

| Entität | Zustand | Wichtige Attribute |
|---|---|---|
| `sensor.inspektion_<id>_letzte` | Datum der letzten Inspektion | `kilometerstand`, `leistung`, `haendler`, `details` (alle Portalfelder), `historie`, `modell`, `modelljahr`, `rueckruf_hinweis`, `roadside_assistance_bis`, `fin`, `abgerufen_am`, `fehler` |
| `sensor.inspektion_<id>_naechste` | berechnetes Fälligkeitsdatum | `tage_bis`, `faellig_bei_km`, `rest_km`, `kilometerstand_aktuell`, `fortschritt_prozent` |
| `sensor.inspektion_<id>_status` | `ok` · `bald fällig` · `überfällig` · `unbekannt` | `rueckruf_offen`, `fehler` |
| `sensor.inspektion_abruf` | Zeitpunkt der letzten Synchronisierung | `naechster_abruf` |

Die Sensoren werden alle 10 Minuten aus dem Cache neu gesetzt; nach einem HA-Neustart sind
sie also spätestens nach 10 Minuten wieder da – ohne erneuten Portalabruf.

## Sofort abrufen

```yaml
action: hassio.addon_stdin
data:
  addon: local_inspektion     # bzw. <hash>_inspektion bei Installation aus GitHub
  input: refresh              # oder "refresh ktm" für ein Fahrzeug
```

Im Package (`homeassistant/packages/inspektion.yaml`) ist dafür das Skript
`script.inspektion_jetzt_abrufen` enthalten.

## Fehlersuche

1. `debug_dump: true` setzen, Add-on neu starten, Button „Jetzt abrufen“.
2. Protokoll des Add-ons prüfen.
3. Dateien unter `/share/inspektion-debug/` ansehen (Samba „share“ oder File Editor):
   `*-page.png` (Screenshot), `*-text.txt`, `*-responses.json`.
4. Findet der Abruf ein Feld nicht, Selektoren in `/share/inspektion/selectors.json`
   überschreiben:

```json
{
  "husqvarna": { "username": "input#signInName", "password": "input#password", "submit": "button#next" },
  "ktm": { "vin": "input#vin" }
}
```

5. Danach `debug_dump` wieder auf `false` und `/share/inspektion-debug/` löschen –
   die Dateien enthalten deine Servicedaten.

## Datenschutz

- Kennwörter liegen nur in der Add-on-Konfiguration (Supervisor, `/data/options.json`)
  und werden nie geloggt; das Kennwortfeld wird vor jedem Screenshot geleert.
- Login-Tokens aus Portalantworten werden verworfen.
- Die FIN erscheint in HA nur maskiert (`VBK…1234`).
- Cookie-Banner: nur notwendige Cookies.
- Abruf 1× monatlich, Fahrzeuge nacheinander – bewusst schonend, nur für eigene Fahrzeuge.
