import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch } from "../api/client";

export default function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await apiFetch("/auth/password-reset/request", {
        method: "POST",
        body: JSON.stringify({ email })
      });
      // Always show success, regardless of whether the email is registered.
      setSubmitted(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Anfrage fehlgeschlagen");
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <h1>Passwort vergessen</h1>
      <p className="subtitle">Wir schicken dir einen Link zum Zurücksetzen.</p>

      <div className="form-card form-card--narrow">
        <div className="icon-chip-lg" aria-hidden="true">
          🔑
        </div>
        {error && <div className="error-banner">{error}</div>}

        {submitted ? (
          <p style={{ textAlign: "center" }}>
            Falls diese E-Mail registriert ist, wurde ein Link zum Zurücksetzen verschickt. Prüfe dein Postfach.
          </p>
        ) : (
          <form onSubmit={handleSubmit}>
            <label htmlFor="email">E-Mail</label>
            <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            <button type="submit" disabled={loading}>
              {loading ? "Wird gesendet…" : "Link anfordern"}
            </button>
          </form>
        )}

        <p style={{ marginTop: "1.5rem", textAlign: "center" }}>
          <Link to="/login" style={{ textDecoration: "none", color: "var(--accent)" }}>
            Zurück zum Login
          </Link>
        </p>
      </div>
    </>
  );
}
