#!/usr/bin/env bash
# Bootstraps backend/.env for a first-time self-hosted setup.
# See SELF_HOSTING.md for the full guide.
set -euo pipefail

cd "$(dirname "$0")"

ENV_FILE="backend/.env"
ENV_EXAMPLE="backend/.env.example"

if [ -f "$ENV_FILE" ]; then
  echo "$ENV_FILE existiert bereits - wird nicht überschrieben."
else
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  echo "$ENV_FILE aus $ENV_EXAMPLE erstellt."
fi

if grep -q "^SECRET_KEY=change-me-to-a-long-random-string$" "$ENV_FILE"; then
  RANDOM_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))" 2>/dev/null \
    || openssl rand -base64 48 | tr -d '\n')
  # Portable in-place sed for both GNU and BSD/macOS sed
  sed -i.bak "s|^SECRET_KEY=.*|SECRET_KEY=${RANDOM_KEY}|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
  echo "Zufälligen SECRET_KEY gesetzt."
else
  echo "SECRET_KEY bereits gesetzt - unverändert gelassen."
fi

cat <<'EOF'

Nächste Schritte:
  1. backend/.env prüfen/anpassen (CORS_ORIGINS, ANTHROPIC_API_KEY, SMTP_*, ...)
     -> siehe SELF_HOSTING.md für alle Optionen.
  2. docker compose up -d --build
  3. docker compose exec backend alembic upgrade head
  4. Frontend öffnen und die erste Familie registrieren.
EOF
