# Familienplaner

Familienkalender, Essensplan, Aufgaben & Punkte/Taschengeld — für Eltern und Kinder gleichermassen bedienbar.

Stack: **PWA** (React + Vite) · **FastAPI** · **PostgreSQL** · **Redis** · Hosting hybrid (CH-Cloud oder Self-Hosting).

## Lokale Entwicklung ohne Docker

Backend:

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate   # Windows: .venv\Scripts\activate
URLLIB3_NO_OVERRIDE=1 pip install --no-binary urllib3-future -r requirements-dev.txt
# inkl. pytest/ruff/bandit; nur requirements.txt reicht für reinen Betrieb.
# Das URLLIB3_NO_OVERRIDE=1 ist Pflicht: caldav -> niquests -> urllib3-future
# überschreibt sonst das echte urllib3-Paket auf der Platte und bricht
# testcontainers/docker-py (Windows: "'NpipeSocket' object has no attribute 'type'").
cp .env.example .env     # SECRET_KEY anpassen
alembic upgrade head     # wendet die bestehende Migration an
uvicorn app.main:app --reload
```

API läuft auf `http://localhost:8000`, interaktive Doku unter `/docs`.

Frontend:

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

App läuft auf `http://localhost:5173`.

## Mit Docker Compose (lokal, Docker Desktop)

```bash
cp backend/.env.example backend/.env   # SECRET_KEY anpassen
npm run up                             # = docker compose up -d --build
npm run migrate                        # erste Migration existiert bereits, wendet sie an
```

Frontend: `http://localhost:8090` · API: `http://localhost:8000` (Docs: `/docs`) · Postgres: `localhost:5433`
(Ports bewusst nicht auf den Standard-Werten 8080/5432 — beide waren auf der Referenzmaschine belegt bzw.
von Windows reserviert; bei Bedarf in `docker-compose.yml` anpassen.)

Weitere Root-Kommandos: `npm run down` (stoppen), `npm run reset` (stoppen + Datenbank-Volume löschen),
`npm run logs`, `npm run migrate:new -- "beschreibung"` (neue Migration erzeugen).

Dasselbe Compose-Bundle läuft unverändert auf einem Schweizer Cloud-Server (Infomaniak/Exoscale) oder
einer Heim-NAS — nur `backend/.env` unterscheidet sich (Domain, Secrets).

## Tests

Backend-Tests starten für jeden Testlauf einen echten PostgreSQL-Container über Docker Desktop
(`testcontainers`) — Docker muss laufen.

```bash
npm test              # Backend (pytest) + Frontend (vitest)
npm run test:backend  # nur Backend
npm run test:frontend # nur Frontend
```

## Lint & Security-Scans

```bash
npm run lint      # ruff (Backend) + tsc --noEmit (Frontend)
npm run security  # bandit + pip-audit (Backend) + npm audit (Frontend)
```

Bekannter, upstream ungefixter Fund: `ecdsa` (transitive Abhängigkeit von `python-jose`) hat eine
Timing-Angriff-CVE ohne Fix-Version — betrifft nur den ECDSA-JWT-Algorithmus, den dieses Projekt nicht
verwendet (wir signieren ausschliesslich mit HS256). Akzeptiertes, beobachtetes Risiko.

`POST /calendar/sync/caldav` nimmt eine vom Nutzer angegebene `server_url` entgegen; Backend und Worker
machen ausgehende Requests dorthin. Da mehrere Familien inzwischen dieselbe Instanz teilen (`family_id`
ist überall die Mandantengrenze), ist das ein echter SSRF-Angriffsvektor und nicht nur ein theoretischer —
`app/core/network_guard.py` validiert die aufgelöste IP daher vor jedem Connect (und erneut vor jedem
periodischen Sync): Loopback, Link-Local/Cloud-Metadata (`169.254.0.0/16`) sind immer blockiert; private
Adressbereiche (RFC1918) nur wenn `HOSTING_MODE=ch_cloud` (geteilte Instanz) — im `self_hosted`-Modus darf
eine Familie weiterhin auf ihren eigenen Nextcloud-Server im selben LAN zugreifen. Bekannte Grenze: die
Prüfung löst den Hostnamen zum Verbindungszeitpunkt auf und ist damit nicht gegen DNS-Rebinding gehärtet.

Alle Kommandos sind einzelne, deterministische Prozesse mit aussagekräftigem Exit-Code — geeignet, um sie
aus einer Agent-/Loop-Automatisierung heraus wiederholt aufzurufen (z.B. `/loop npm test`).

## Browser-Vorschau für Agents

`.claude/launch.json` definiert drei Vorschau-Ziele für die Claude-Code-Browser-Preview: `frontend-dev`
(startet `npm run dev` im Frontend), `docker-frontend` und `docker-backend-docs` (setzen einen laufenden
`npm run up`-Stack voraus).

## Projektstruktur

```
backend/   FastAPI-App, SQLAlchemy-Models, Alembic-Migrationen, Sync-Worker, pytest-Suite
frontend/  React + Vite PWA, Vitest-Suite
package.json  Root-Orchestrierung (Docker, Tests, Lint, Security) — kein eigenständiges npm-Paket
```

## Selbst hosten

Siehe [SELF_HOSTING.md](SELF_HOSTING.md) für eine vollständige Anleitung (Konfiguration, Backup, HTTPS,
Updates) über die lokale Docker-Desktop-Nutzung hinaus. Beispiel-Manifeste für Kubernetes liegen unter
[k8s/](k8s/README.md).

## Lizenz

[MIT](LICENSE)

## Stand

Umgesetzt: Familienkonto & Rollen, Kinderprofile mit PIN-Login, Familienadmin-Dashboard, Kind-Dashboard,
nativer Kalender mit Google- und CalDAV-Sync, Essensplan mit Rezeptvorschlägen aus Wunschzutaten,
automatische Einkaufsliste, Aufgaben mit Basis-/Bonus-/Geld-Kategorie und Punkte-/Verdienst-Tracking,
Stundenplan- und Elternbrief-Digitalisierung per Bild-Upload (Anthropic oder lokales Ollama-Modell),
automatisch aus dem Stundenplan generierte Kalendertermine, Passwort-Reset-Flow für Eltern, Web-Push-
Benachrichtigung bei neu zugewiesenen Aufgaben.

Noch offen: weitere Push-Auslöser (Kalender-Erinnerungen, Brief-Rückfragen), automatisiertes Backup.
