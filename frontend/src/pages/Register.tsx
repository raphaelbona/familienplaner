import { FormEvent, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { apiFetch, decodeFamilyIdFromToken, setFamilyId, setToken } from "../api/client";
import PasswordInput from "../components/PasswordInput";

export default function Register() {
  const navigate = useNavigate();
  const [familyName, setFamilyName] = useState("");
  const [adminName, setAdminName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { access_token } = await apiFetch<{ access_token: string }>("/auth/register-family", {
        method: "POST",
        body: JSON.stringify({
          family_name: familyName,
          admin_display_name: adminName,
          admin_email: email,
          admin_password: password,
        }),
      });
      setToken(access_token);
      const familyId = decodeFamilyIdFromToken(access_token);
      if (familyId) {
        setFamilyId(familyId);
      }
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registrierung fehlgeschlagen");
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <h1>Neue Familie registrieren</h1>
      <p className="subtitle">Erstelle ein Familienkonto und lade später weitere Eltern und Kinder ein.</p>

      <div className="form-card form-card--narrow">
        <div className="icon-chip-lg" aria-hidden="true">
          🏡
        </div>
        {error && <div className="error-banner">{error}</div>}
        <form onSubmit={handleSubmit}>
          <label htmlFor="family-name">Familienname</label>
          <input
            id="family-name"
            type="text"
            value={familyName}
            onChange={(e) => setFamilyName(e.target.value)}
            required
          />
          <label htmlFor="admin-name">Dein Name</label>
          <input
            id="admin-name"
            type="text"
            value={adminName}
            onChange={(e) => setAdminName(e.target.value)}
            required
          />
          <label htmlFor="email">E-Mail</label>
          <input
            id="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          <label htmlFor="password">Passwort</label>
          <PasswordInput
            id="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          <button type="submit" disabled={loading}>
            {loading ? "Registriert…" : "Registrieren"}
          </button>
        </form>
        <p className="text-center" style={{ marginTop: "1.5rem", fontSize: "0.9rem" }}>
          Schon ein Konto?{" "}
          <Link to="/login" style={{ textDecoration: "none", color: "var(--accent)" }}>
            Anmelden
          </Link>
        </p>
      </div>
    </>
  );
}
