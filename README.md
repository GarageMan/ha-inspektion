# Motorrad-Inspektionen für Home Assistant (KTM & Husqvarna)

Home-Assistant-Add-on, das **monatlich** die letzte Inspektion aus
- **KTM** Service & Sicherheitscheck (FIN + Kennwort) und
- **Husqvarna** Serviceheft auf my.husqvarna-motorcycles.com (E-Mail + Kennwort)

ausliest und als Sensoren mit **allen verfügbaren Angaben** bereitstellt. Anzeige im
HA-Dashboard, Benachrichtigung aufs Handy, optional als Fenster auf dem Windows-Desktop.

---

## 1. Warum Home Assistant statt eigener Web-App

| | Eigene Web-App (v1) | Home-Assistant-Add-on (v2) |
|---|---|---|
| Betrieb | eigener Container + Port + Auth | läuft im vorhandenen HA, kein zusätzlicher Webserver |
| Anzeige | eigenes Dashboard | HA-Dashboard (PC, Handy-App, Tablet) |
| Erinnerung | – | Push über HA Companion App |
| Kilometerstand | eigenes Formular | `input_number`-Helfer, auch per Automation befüllbar |
| Zugriff unterwegs | VPN/Reverse Proxy nötig | wie bisher für HA (App/Nabu Casa/VPN) |
| Abruf 1×/Monat | Webserver läuft 24/7 ungenutzt | Add-on schläft bis zum Abruftag |
| Aufwand | höher | geringer |

## 2. Architektur

```
┌──────────────────────── Raspberry Pi 4 · HAOS ─────────────────────────┐
│                                                                        │
│  Add-on "Motorrad-Inspektionen"                                        │
│   ├─ Zeitplan: 1. des Monats 06:17 (+ "refresh" über stdin)            │
│   ├─ Playwright/Chromium ── Login ──►  ktm.com (FIN + Kennwort)        │
│   │                       ── Login ──►  my.husqvarna-motorcycles.com   │
│   │                                     (E-Mail + Kennwort)            │
│   ├─ parser.py: letzte Inspektion + alle Felder, Historie, Rückruf     │
│   ├─ planner.py: nächste Inspektion (Monate/km, km-Stand-Helfer)       │
│   └─ /data/cache.json ──(alle 10 min)──► Core-API /api/states          │
│                                                │                       │
│  Home Assistant Core ◄─────────────────────────┘                       │
│   ├─ sensor.inspektion_<id>_letzte / _naechste / _status               │
│   ├─ input_number.<id>_kilometerstand                                  │
│   ├─ Automationen → Push "bald fällig", "Rückruf", "Abruf-Fehler"      │
│   └─ Dashboard "Motorräder"                                            │
└───────────────┬────────────────────────────────────────────────────────┘
                ▼
     Windows 11 (Edge-App-Fenster) · Handy (Companion App)
```

## 3. Inhalt

| Pfad | Zweck |
|---|---|
| `repository.yaml` | Add-on-Repository (für GitHub-Installation) |
| `inspektion/` | Das Add-on (config.yaml, Dockerfile, Python-Code, DOCS.md) |
| `homeassistant/packages/inspektion.yaml` | km-Helfer, Skript „Jetzt abrufen“, 3 Automationen |
| `homeassistant/dashboard-view.yaml` | Dashboard-Ansicht „Motorräder“ |
| `windows/Install-InspektionDesktop.ps1` | Desktop-Fenster + Autostart |
| `standalone/` | Variante B: gleicher Code als Container auf mein-server |
| `tests/` | Unit-Tests (Parser, Planer, Zeitplan) |

## 4. Installation (Variante A – Add-on auf HAOS)

### 4.1 Add-on einspielen

**Lokal (schnellster Weg):**
1. Add-on *Samba share* oder *Advanced SSH & Web Terminal* aktiv.
2. Ordner `inspektion/` nach `\\homeassistant\addons\inspektion\` kopieren
   (SSH: `/addons/inspektion/`).
3. *Einstellungen → Add-ons → Add-on-Store → ⋮ → Nach Updates suchen* →
   unter *Lokale Add-ons* erscheint **Motorrad-Inspektionen** → *Installieren*.
   Der erste Build auf dem Pi 4 dauert ca. 10–20 Minuten (Chromium wird installiert,
   Image ca. 1 GB).

**Über GitHub (wie dein Homelab-Dashboard):** Inhalt als Repo `GarageMan/ha-inspektion`
veröffentlichen, in HA unter *Add-on-Store → ⋮ → Repositories* hinzufügen. Der Slug lautet
dann `<hash>_inspektion` statt `local_inspektion`.

### 4.2 Konfigurieren
Tab *Konfiguration* → Fahrzeuge eintragen (siehe `inspektion/DOCS.md`):
- KTM: `portal: ktm_service_check`, `vin`, `password`
- Husqvarna: `portal: husqvarna_servicebook`, `username` (E-Mail), `password`
- Für die Einrichtung `debug_dump: true`.

Starten → *Protokoll* prüfen. Erwartet:
```
ktm: letzte Inspektion 2025-11-10, 9850 km (2 Einträge)
husqvarna: letzte Inspektion 2026-04-15, 6200 km (3 Einträge)
```

### 4.3 Package und Dashboard
```yaml
# configuration.yaml
homeassistant:
  packages: !include_dir_named packages
```
1. `homeassistant/packages/inspektion.yaml` nach `/config/packages/` kopieren,
   `notify.mobile_app_DEIN_HANDY` und ggf. den Add-on-Slug anpassen.
2. *Entwicklerwerkzeuge → YAML → Konfiguration prüfen* → *Neu starten*.
3. *Einstellungen → Dashboards → Dashboard hinzufügen* → Name „Motorrad“,
   URL `dashboard-motorrad` → bearbeiten → Raw-Editor → Inhalt von
   `homeassistant/dashboard-view.yaml` unter `views:` einfügen.
4. In der Add-on-Konfiguration `odometer_entity` auf die beiden `input_number`-Helfer setzen.

### 4.4 Windows-Desktop
```powershell
powershell -ExecutionPolicy Bypass -File .\windows\Install-InspektionDesktop.ps1 `
  -Url "http://homeassistant.local:8123/dashboard-motorrad/motorraeder" -Autostart
```
Öffnet die Ansicht als randloses Edge-Fenster (440 × 760). Beim ersten Start einmal bei
HA anmelden und „Angemeldet bleiben“ wählen.

## 5. Variante B – Container auf mein-server

Wenn der Pi entlastet werden soll (Chromium braucht beim Abruf kurzzeitig ca. 300–500 MB RAM):
```bash
cd standalone
cp options.json.example options.json && chmod 600 options.json && nano options.json
printf '%s' 'LONG-LIVED-TOKEN-AUS-HA-PROFIL' > ha_token.txt && chmod 600 ha_token.txt
mkdir -p data && docker compose up -d --build
docker compose logs -f
```
Token: HA → Profil → *Sicherheit* → *Langlebige Zugriffstoken* → erstellen.
Das Skript „Jetzt abrufen“ funktioniert in dieser Variante nicht – stattdessen
`docker compose restart inspektion` (ruft ab, wenn Daten älter als `max_age_days`)
oder `docker compose exec -T inspektion sh -c 'echo refresh > /proc/1/fd/0'`.

## 6. Kurzreferenz

| Aufgabe | Wie |
|---|---|
| Sofort abrufen | Button „Jetzt abrufen“ bzw. `script.inspektion_jetzt_abrufen` |
| Nur KTM abrufen | `hassio.addon_stdin` mit `input: refresh ktm` |
| Abrufzeitpunkt ändern | Add-on-Option `schedule_day` / `schedule_time` |
| km-Stand pflegen | `input_number.<id>_kilometerstand` im Dashboard |
| Fehlersuche | `debug_dump: true` → `/share/inspektion-debug/` |
| Selektoren anpassen | `/share/inspektion/selectors.json` |
| Tests | `cd inspektion && python -m pytest ../tests -q` |

## 7. Hinweise

- Weder KTM noch Husqvarna bieten eine offizielle API. Der Abruf bedient die Webseiten
  wie ein Browser und liest die Antworten aus. Ändern die Hersteller ihre Seiten, kann
  eine Anpassung nötig werden; das Add-on meldet das per Automation.
- Die „nächste Inspektion“ ist **berechnet** (letzte Inspektion + Intervall aus der
  Bedienungsanleitung), nicht vom Hersteller geliefert.
- Abruf 1× monatlich und nur für eigene Fahrzeuge – automatisierter Zugriff ist in den
  Nutzungsbedingungen der Portale nicht ausdrücklich vorgesehen.
