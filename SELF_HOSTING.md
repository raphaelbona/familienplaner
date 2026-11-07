# Familienplaner selbst hosten

Diese Anleitung richtet sich an alle, die den Familienplaner dauerhaft betreiben wollen — auf einer
Heim-NAS, einem Mini-PC oder einem Schweizer Cloud-Server (Infomaniak/Exoscale) — statt ihn nur lokal auf
Docker Desktop laufen zu lassen. Die Grundlage ist überall dieselbe: das Docker-Compose-Bundle aus diesem
Repository.

## Voraussetzungen

- Docker und Docker Compose v2 (`docker compose version`)
- Ein Rechner mit dauerhaft laufendem Docker-Daemon (NAS, Server, Mini-PC)
- Optional, aber empfohlen für einen produktiven Betrieb: eine Domain + Reverse Proxy mit HTTPS
  (siehe [HTTPS / Reverse Proxy](#https--reverse-proxy) unten)

## Erstes Setup

```bash
git clone <dieses-repo> familienplaner
cd familienplaner
./setup.sh
```

`setup.sh` legt `backend/.env` aus `backend/.env.example` an (falls noch nicht vorhanden) und generiert
einen zufälligen `SECRET_KEY`. Danach:

```bash
docker compose up -d --build
docker compose exec backend alembic upgrade head
```

Die App läuft jetzt unter `http://<server>:8090` (Frontend) und `http://<server>:8000` (API). Öffne die
App und registriere die erste Familie — der erste registrierte Account wird automatisch `parent_admin`.

## Konfiguration (`backend/.env`)

| Variable | Zweck | Pflicht? |
|---|---|---|
| `SECRET_KEY` | Signiert JWTs und OAuth-State-Tokens | Ja — lang & zufällig, nie committen |
| `DATABASE_URL`, `REDIS_URL` | Interne Service-Verbindungen | Nein, Defaults passen zu `docker-compose.yml` |
| `HOSTING_MODE` | `self_hosted` (eine Familie/Instanz) oder `ch_cloud` (mehrere Familien teilen sich eine Instanz) | Ja — steuert u.a. die CalDAV-SSRF-Härtung (siehe README) |
| `CORS_ORIGINS` | Erlaubte Frontend-Origins als JSON-Array | Ja, muss die tatsächliche Frontend-URL enthalten |
| `GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI` | Google-Kalender-Sync (siehe Abschnitt „Google-Kalender“) | Nein — ohne das bleibt der Google-Sync deaktiviert (503, die Kalenderseite zeigt „Google ist noch nicht eingerichtet“) |
| `VISION_PROVIDER` | `anthropic` (Standard) oder `ollama` | Nein |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | Stundenplan-/Brief-Digitalisierung über die Anthropic-API | Nein — ohne Key bleibt die Funktion deaktiviert (503) |
| `OLLAMA_BASE_URL`, `OLLAMA_VISION_MODEL` | Lokale, abo-freie Alternative zu Anthropic (siehe unten) | Nur bei `VISION_PROVIDER=ollama` |
| `APP_BASE_URL` | Öffentliche Frontend-URL: Ziel der Weiterleitung nach dem Google-Login (`<APP_BASE_URL>/kalender`), Links in E-Mails; bei `https://` wird das State-Cookie des Google-Logins als `Secure` gesetzt | Ja, falls Google-Sync oder Passwort-Reset genutzt wird |
| `APP_TIMEZONE` | Zeitzone, in der ganztägige Google-Termine verankert werden (Standard `Europe/Zurich`) | Nein |
| `GOOGLE_SYNC_POLL_MINUTES` | Intervall des Google-Imports im Worker (Standard 15) | Nein |
| `SMTP_HOST/PORT/USER/PASSWORD/FROM` | Versand von Passwort-Reset-E-Mails | Nein — ohne SMTP wird der Reset-Link stattdessen in die Backend-Logs geschrieben (`docker compose logs backend`) |

## Google-Kalender (Import Google → Familienplaner)

Der Sync ist **einseitig**: Termine werden aus Google gelesen (Scope `calendar.readonly`), nie zurückgeschrieben.
Importiert wird ein Fenster von 7 Tagen zurück bis 90 Tage voraus, der Worker gleicht alle 15 Minuten ab; in der App
gibt es zusätzlich „Jetzt synchronisieren“. Gelöschte oder abgesagte Google-Termine verschwinden beim nächsten Abgleich.

**Einrichtung in der Google Cloud Console**

1. Projekt anlegen (oder ein bestehendes wählen) und unter „APIs & Dienste → Bibliothek“ die **Google Calendar API**
   aktivieren.
2. „OAuth-Zustimmungsbildschirm“ einrichten: Nutzertyp „Extern“, App-Name, Support-E-Mail; Scope
   `https://www.googleapis.com/auth/calendar.readonly` hinzufügen. Solange die App im Status „Testing“ ist, müssen die
   Google-Konten der Eltern als Testnutzer eingetragen sein.
3. „Anmeldedaten → OAuth-Client-ID erstellen“, Anwendungstyp **„Webanwendung“** (nicht „Desktop“).
4. Als autorisierten Weiterleitungs-URI eintragen: `https://<api-host>/calendar/sync/google/callback` (z.B.
   `https://api.example.com/calendar/sync/google/callback`) — exakt so, wie er in `GOOGLE_REDIRECT_URI` steht.
5. In `backend/.env` setzen und `docker compose restart backend worker`:

```env
GOOGLE_CLIENT_ID=<client-id>.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=<client-secret>
GOOGLE_REDIRECT_URI=https://<api-host>/calendar/sync/google/callback
APP_BASE_URL=https://<frontend-host>
```

Danach: Kalenderseite → „Mit Google verbinden“ → Kalender auswählen und Mitglieder zuordnen.

- **Frontend und API auf verschiedenen Origins** (z.B. `app.…` und `api.…` derselben Domain) funktionieren: der Verbinden-Aufruf
  sendet Cookies mit (`credentials: 'include'`, `CORS_ORIGINS` muss die Frontend-Origin explizit enthalten) und das
  State-Cookie wird bei `https://` in `APP_BASE_URL` als `Secure`, `HttpOnly`, `SameSite=Lax` gesetzt. Beide Hosts müssen
  zur selben Site gehören (gleiche registrierbare Domain).
- **Testing vs. Produktion:** Im Status „Testing“ laufen Refresh-Tokens laut Google nach 7 Tagen ab — die App zeigt dann
  „Neu verbinden nötig“. Für dauerhaften Betrieb den Zustimmungsbildschirm auf „In Produktion“ stellen; für den
  sensiblen Scope kann Google eine App-Verifizierung verlangen (prüfen, ob für den reinen Familiengebrauch nötig).
- **Zuordnung der Termine:** Titel-Tag `Mia: Zahnarzt`, `Mia, Leo: Schwimmen` oder `Zahnarzt #Mia` ordnet einen Termin
  Mitgliedern zu (Name = Anzeigename, Gross-/Kleinschreibung egal); sonst gelten Teilnehmer mit der E-Mail eines Elternteils,
  dann die beim Kalender eingestellten Mitglieder, sonst die ganze Familie.
- **Datenschutz:** Kinder und das Wand-Dashboard sehen bei Google-Terminen, die nur Eltern betreffen oder in Google als
  „privat“/„vertraulich“ markiert sind, nur „Belegt“ (serverseitig erzwungen).
- „Trennen“ widerruft den Google-Zugriff (best effort) und entfernt die Verbindung; die importierten Termine können dabei
  mit entfernt oder als normale Termine behalten werden.
- **Ferien und freie Tage:** Ganztägige, nicht private Google-Termine, deren Titel „Ferien“ enthält (Herbstferien, „Ferien: Herbst“) oder
  mit „Schulfrei“, „Schule frei“ oder „Unterrichtsfrei“ beginnt, werden statt als Termin als Ferien importiert (Seite Stundenplan →
  „Ferien und freie Tage“; ein Namens-Präfix wie „Mia: Ferien“ beschränkt sie auf dieses Kind, sonst gelten sie für alle Kinder). Dort
  fallen die Stundenplan-Termine weg und die Tagesansicht zeigt die Ferien. Diese Einträge werden nur in Google geändert (sie
  verschwinden mit dem Google-Termin, oder wenn der Kalender abgewählt bzw. Google getrennt wird). Bekannte Grenze: auch ein
  ganztägiger „Ferienwohnung buchen“ gilt als Ferien. Ferien lassen sich auf der Stundenplan-Seite auch von Hand eintragen.

## Lokales LLM statt Anthropic-API (kein Abo)

Für die Stundenplan-/Brief-Digitalisierung kann statt der Anthropic-API ein selbst gehostetes,
vision-fähiges Modell über [Ollama](https://ollama.com) verwendet werden:

```bash
docker compose --profile local-llm up -d ollama
docker compose exec ollama ollama pull llama3.2-vision
```

Danach in `backend/.env`: `VISION_PROVIDER=ollama` und `OLLAMA_VISION_MODEL=llama3.2-vision` setzen,
dann `docker compose restart backend`. Die Erkennungsqualität ist spürbar schwächer als bei Claude,
dafür verlassen die Bilder nie den eigenen Server und es entstehen keine API-Kosten. Für brauchbare
Antwortzeiten wird eine GPU empfohlen — auf reiner CPU kann die Auswertung eines Bildes mehrere
Minuten dauern.

**Speicherbedarf beachten:** `llama3.2-vision` (7.8 GB) und `llava` (4.7 GB) brauchen für den
Docker-Desktop-VM mehrere GB RAM zum Laden — bei zu wenig zugewiesenem Speicher terminiert der
Ollama-Prozess mit einem `killed`-Fehler (in den Backend-Logs als 503 sichtbar). Prüfe das aktuelle
Limit mit `docker info --format '{{.MemTotal}}'` und erhöhe es bei Bedarf unter Docker Desktop →
Settings → Resources → Memory (mind. 8 GB empfohlen, eher 16 GB für flüssigen Betrieb). Passt das
Limit nicht, funktioniert testweise auch das deutlich kleinere `moondream` (~1.7 GB,
`OLLAMA_VISION_MODEL=moondream`) — es läuft bereits mit rund 4 GB, liefert bei echten Fotos aber eine
merklich unzuverlässigere Texterkennung als die größeren Modelle.

## Push-Benachrichtigungen (VAPID-Schlüssel)

Ohne konfigurierte VAPID-Schlüssel bleibt "Benachrichtigungen aktivieren" im Frontend inaktiv (kein
Fehler, die Funktion wird einfach nicht angeboten). Ein Schlüsselpaar generieren:

```bash
docker compose exec backend python -c "
from py_vapid import Vapid02
import base64

v = Vapid02()
v.generate_keys()

def b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()

pub = v.public_key.public_numbers()
pub_raw = b'\x04' + pub.x.to_bytes(32, 'big') + pub.y.to_bytes(32, 'big')
print('VAPID_PUBLIC_KEY=' + b64url(pub_raw))

priv = v.private_key.private_numbers()
print('VAPID_PRIVATE_KEY=' + b64url(priv.private_value.to_bytes(32, 'big')))
"
```

Beide Werte in `backend/.env` eintragen (`VAPID_SUBJECT` auf eine echte `mailto:`-Adresse setzen) und
`docker compose restart backend` — danach funktioniert der "Benachrichtigungen aktivieren"-Button.

## HTTPS / Reverse Proxy

Für den produktiven Betrieb außerhalb von `localhost` sollte ein Reverse Proxy (Caddy, nginx-proxy,
Traefik) mit TLS-Zertifikat vor den `frontend`-Container (Port 8090) und `backend`-Container (Port 8000)
gesetzt werden. Wichtig dabei:

- `GOOGLE_REDIRECT_URI` und `APP_BASE_URL` müssen auf die öffentliche HTTPS-Domain zeigen, nicht auf
  `localhost`.
- `CORS_ORIGINS` muss die öffentliche Frontend-Domain enthalten.
- Google Cloud Console: die Redirect-URI muss dort exakt so hinterlegt sein wie in `GOOGLE_REDIRECT_URI`.

## Backup

Alle persistenten Daten liegen in zwei benannten Docker-Volumes:

```bash
docker run --rm -v familienplaner_db_data:/data -v $(pwd):/backup alpine \
  tar czf /backup/db_data-backup.tar.gz -C /data .
docker run --rm -v familienplaner_uploads_data:/data -v $(pwd):/backup alpine \
  tar czf /backup/uploads_data-backup.tar.gz -C /data .
```

Für die Datenbank ist ein `pg_dump` gegen Port `5433` (siehe `docker-compose.yml`) die robustere
Alternative zum reinen Volume-Backup, falls regelmäßige Backups automatisiert werden sollen.

## Updates

```bash
git pull
docker compose build backend worker frontend
docker compose up -d
docker compose exec backend alembic upgrade head
```

Migrationen werden nicht automatisch beim Start ausgeführt — `alembic upgrade head` nach jedem Update
nicht vergessen.

## Bekannte Grenzen

- Push-Benachrichtigungen werden aktuell nur bei neu zugewiesenen Aufgaben ausgelöst, nicht für
  Kalender-Erinnerungen oder Brief-Rückfragen.
- Kein automatisiertes Backup — die obigen Befehle sind manuell auszuführen oder per Cronjob einzuplanen.
