import { useEffect, useState } from "react";
import { Navigate, useNavigate, Link } from "react-router-dom";
import {
  ApiError,
  DEVICE_UNAUTHORIZED_MESSAGE,
  apiFetch,
  clearDeviceToken,
  getDeviceToken,
  getFamilyId,
  setToken,
} from "../api/client";

interface PinAccount {
  id: string;
  display_name: string;
  avatar_key: string | null;
  role: "child" | "dashboard";
}

/** Privacy: children are only shown on devices a parent approved. An unpaired device gets the parent login and
 *  never even asks the server about children. */
export default function ChildLogin() {
  if (!getDeviceToken() || !getFamilyId()) return <Navigate to="/login" replace />;
  return <PairedChildLogin />;
}

function isDeviceRejected(err: unknown): boolean {
  return err instanceof ApiError && err.status === 401 && err.message === DEVICE_UNAUTHORIZED_MESSAGE;
}

function PairedChildLogin() {
  const navigate = useNavigate();
  const familyId = getFamilyId();
  const deviceToken = getDeviceToken();
  const deviceHeaders = { "X-Device-Token": deviceToken ?? "" };
  const [accounts, setAccounts] = useState<PinAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedAccountId, setSelectedAccountId] = useState<string | null>(null);
  const [pin, setPin] = useState("");
  const [pinError, setPinError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // Too many wrong PINs: the server answers 429 + Retry-After; the button stays disabled until then.
  const [lockedUntil, setLockedUntil] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const locked = lockedUntil !== null && now < lockedUntil;

  useEffect(() => {
    if (lockedUntil === null) return;
    const timer = setInterval(() => {
      const current = Date.now();
      setNow(current);
      if (current >= lockedUntil) {
        setLockedUntil(null);
        setPinError(null);
      }
    }, 500);
    return () => clearInterval(timer);
  }, [lockedUntil]);

  // Fetch PIN login accounts for the shared tablet: children and optional wall dashboard.
  useEffect(() => {
    async function fetchAccounts() {
      if (!familyId) {
        setLoading(false);
        return;
      }
      try {
        const response = await apiFetch<PinAccount[]>(`/families/${familyId}/pin-accounts`, {
          method: "GET",
          headers: deviceHeaders,
        });
        setAccounts(response);
      } catch (err) {
        if (isDeviceRejected(err)) {
          // The parent revoked this device or it expired: forget it and fall back to the parent login.
          clearDeviceToken();
          navigate("/login", { replace: true });
          return;
        }
        setError(err instanceof Error ? err.message : "Fehler beim Laden der PIN-Konten");
      } finally {
        setLoading(false);
      }
    }

    fetchAccounts();
  }, [familyId]);

  // Handle PIN submission
  async function handlePinSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedAccountId || !familyId) return;

    setPinError(null);
    setSubmitting(true);
    try {
      const { access_token } = await apiFetch<{ access_token: string }>("/auth/pin", {
        method: "POST",
        headers: deviceHeaders,
        body: JSON.stringify({
          family_id: familyId,
          user_id: selectedAccountId,
          pin,
        }),
      });
      setToken(access_token);
      navigate("/");
    } catch (err) {
      if (isDeviceRejected(err)) {
        clearDeviceToken();
        navigate("/login", { replace: true });
        return;
      }
      if (err instanceof ApiError && err.status === 429) {
        // Not a device problem: keep the device token, just wait.
        const seconds = err.retryAfterSeconds ?? 60;
        setNow(Date.now());
        setLockedUntil(Date.now() + seconds * 1000);
        setPin("");
        setPinError(err.message);
        return;
      }
      setPinError(err instanceof Error ? err.message : "PIN stimmt nicht, nochmal versuchen");
    } finally {
      setSubmitting(false);
    }
  }

  // Loading children
  if (loading) {
    return (
      <>
        <h1>Kinder-Login</h1>
        <div className="form-card form-card--narrow" style={{ textAlign: "center" }}>
          <p>Lädt…</p>
        </div>
      </>
    );
  }

  // Error loading children
  if (error) {
    return (
      <>
        <h1>Kinder-Login</h1>
        <div className="form-card form-card--narrow">
          <div className="error-banner">{error}</div>
          <p style={{ marginTop: "1.5rem" }}>
            <Link to="/login">Zurück zum Eltern-Login</Link>
          </p>
        </div>
      </>
    );
  }

  // Account selection (no account selected yet)
  if (!selectedAccountId) {
    return (
      <>
        <h1>Wer nutzt das Tablet?</h1>
        <p className="subtitle">Wähle Kind oder Wand-Dashboard.</p>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: "1rem" }}>
          {accounts.map((account) => (
            <button
              key={account.id}
              onClick={() => setSelectedAccountId(account.id)}
              style={{
                border: "1.5px solid var(--border)",
                background: "var(--surface-2)",
                borderRadius: "1rem",
                padding: "1.1rem",
                minHeight: "120px",
                cursor: "pointer",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                gap: "0.5rem",
                fontSize: "1rem",
              }}
            >
              <div style={{ fontSize: "3.2rem" }} aria-hidden="true">
                {account.avatar_key || (account.role === "dashboard" ? "📋" : "🧒")}
              </div>
              <div style={{ fontSize: "1rem", fontWeight: "700" }}>{account.display_name}</div>
              {account.role === "dashboard" && (
                <div style={{ color: "var(--text-muted)", fontSize: "12.5px", fontWeight: 700 }}>
                  Wand-Dashboard
                </div>
              )}
            </button>
          ))}
        </div>
      </>
    );
  }

  // PIN entry
  const selectedAccount = accounts.find((c) => c.id === selectedAccountId);
  return (
    <>
      <h1>PIN eingeben</h1>
      <p className="subtitle">{selectedAccount?.display_name}, gib die 4-stellige PIN ein.</p>

      <div className="form-card form-card--narrow">
        <div className="icon-chip-lg" aria-hidden="true">
          {selectedAccount?.avatar_key || (selectedAccount?.role === "dashboard" ? "📋" : "🧒")}
        </div>
        {pinError && (
          <div className="error-banner" role={locked ? "alert" : undefined}>
            {pinError}
          </div>
        )}
        <form onSubmit={handlePinSubmit}>
          <label htmlFor="pin">PIN</label>
          <input
            id="pin"
            type="text"
            inputMode="numeric"
            maxLength={4}
            value={pin}
            onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))}
            required
            style={{ textAlign: "center", letterSpacing: "0.3em", fontSize: "1.5rem" }}
          />
          <button type="submit" disabled={submitting || locked || pin.length !== 4}>
            {submitting ? "Anmeldet…" : "Anmelden"}
          </button>
        </form>
        <p style={{ marginTop: "1.5rem", textAlign: "center" }}>
          <button
            onClick={() => {
              setSelectedAccountId(null);
              setPin("");
              setPinError(null);
              setLockedUntil(null);
            }}
            style={{
              background: "none",
              border: "none",
              color: "var(--accent)",
              cursor: "pointer",
              textDecoration: "none",
              fontSize: "0.9rem",
            }}
          >
            Zurück zur Avatar-Auswahl
          </button>
        </p>
      </div>
    </>
  );
}
