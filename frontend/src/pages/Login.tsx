import { FormEvent, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { apiFetch, decodeFamilyIdFromToken, isDevicePaired, setFamilyId, setToken } from "../api/client";
import PasswordInput from "../components/PasswordInput";

export default function Login() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { access_token } = await apiFetch<{ access_token: string }>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password })
      });
      setToken(access_token);
      const familyId = decodeFamilyIdFromToken(access_token);
      if (familyId) {
        setFamilyId(familyId);
      }
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login fehlgeschlagen");
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <h1>Anmelden</h1>
      <p className="subtitle">
        {isDevicePaired() ? "Für Eltern. Kinder und Wand-Dashboard melden sich mit PIN an." : "Für Eltern."}
      </p>

      <div className="form-card form-card--narrow">
        <div className="icon-chip-lg" aria-hidden="true">
          👋
        </div>
        {error && <div className="error-banner">{error}</div>}
        <form onSubmit={handleSubmit}>
          <label htmlFor="email">E-Mail</label>
          <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          <label htmlFor="password">Passwort</label>
          <PasswordInput
            id="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          <button type="submit" disabled={loading}>
            {loading ? "Meldet an…" : "Anmelden"}
          </button>
        </form>
        <div style={{ marginTop: "1.5rem", display: "flex", flexDirection: "column", gap: "0.75rem", fontSize: "0.9rem" }}>
          <p style={{ margin: "0", textAlign: "center" }}>
            <Link to="/passwort-vergessen" style={{ textDecoration: "none", color: "var(--accent)" }}>
              Passwort vergessen?
            </Link>
          </p>
          <p style={{ margin: "0", textAlign: "center" }}>
            <Link to="/registrieren" style={{ textDecoration: "none", color: "var(--accent)" }}>
              Neue Familie registrieren
            </Link>
          </p>
          {isDevicePaired() && (
          <p style={{ margin: "0", textAlign: "center" }}>
            <Link to="/kind-login" style={{ textDecoration: "none", color: "var(--accent)" }}>
              Kind oder Wand-Dashboard? Hier anmelden
            </Link>
          </p>
          )}
        </div>
      </div>
    </>
  );
}
