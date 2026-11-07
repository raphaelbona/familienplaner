import { FormEvent, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { apiFetch } from "../api/client";
import PasswordInput from "../components/PasswordInput";

export default function ResetPassword() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await apiFetch("/auth/password-reset/confirm", {
        method: "POST",
        body: JSON.stringify({ token, new_password: newPassword })
      });
      setSuccess(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Zurücksetzen fehlgeschlagen");
    } finally {
      setLoading(false);
    }
  }

  if (!token) {
    return (
      <>
        <h1>Passwort zurücksetzen</h1>
        <div className="form-card form-card--narrow">
          <div className="error-banner">Kein gültiger Link. Bitte fordere einen neuen Link an.</div>
          <p style={{ marginTop: "1.5rem", textAlign: "center" }}>
            <Link to="/passwort-vergessen" style={{ textDecoration: "none", color: "var(--accent)" }}>
              Link erneut anfordern
            </Link>
          </p>
        </div>
      </>
    );
  }

  return (
    <>
      <h1>Passwort zurücksetzen</h1>
      <p className="subtitle">Wähle ein neues Passwort.</p>

      <div className="form-card form-card--narrow">
        <div className="icon-chip-lg" aria-hidden="true">
          🔑
        </div>
        {error && <div className="error-banner">{error}</div>}

        {success ? (
          <>
            <p style={{ textAlign: "center" }}>Passwort wurde erfolgreich zurückgesetzt.</p>
            <p style={{ marginTop: "1.5rem", textAlign: "center" }}>
              <Link to="/login" style={{ textDecoration: "none", color: "var(--accent)" }}>
                Jetzt anmelden
              </Link>
            </p>
          </>
        ) : (
          <form onSubmit={handleSubmit}>
            <label htmlFor="new-password">Neues Passwort</label>
            <PasswordInput
              id="new-password"
              autoComplete="new-password"
              minLength={8}
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
            />
            <button type="submit" disabled={loading}>
              {loading ? "Wird gespeichert…" : "Passwort speichern"}
            </button>
          </form>
        )}
      </div>
    </>
  );
}
