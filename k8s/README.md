# Kubernetes-Beispiel

Beispiel-Manifeste für einen eigenen Cluster. Vor dem Einsatz anpassen:

- `01-postgres.yaml`: `POSTGRES_PASSWORD: CHANGE_ME` durch ein eigenes, langes Passwort ersetzen.
- `04`–`06`: `registry.example.com/your-user/…` durch die eigene Container-Registry ersetzen
  (Images aus `backend/` und `frontend/` selbst bauen und pushen).
- `07-ingress.yaml`: `app.example.com`, `api.example.com` und die Let's-Encrypt-E-Mail `admin@example.com`
  durch die eigenen Werte ersetzen.

Das Secret `backend-env` fehlt bewusst im Repository. Es enthält die Backend-Konfiguration (siehe
`backend/.env.example`: `SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`, `CORS_ORIGINS`, …) und wird so angelegt:

```bash
kubectl -n familienplaner create secret generic backend-env --from-env-file=backend/.env
```

Die `DATABASE_URL` muss zum Passwort aus `01-postgres.yaml` passen.
