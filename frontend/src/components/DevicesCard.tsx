import { useEffect, useState } from "react";
import {
  apiFetch,
  clearDeviceToken,
  decodeFamilyIdFromToken,
  getDeviceId,
  getToken,
  isDevicePaired,
  setDeviceToken,
  setFamilyId,
} from "../api/client";

interface Device {
  id: string;
  label: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string;
  revoked: boolean;
}

interface PairedDevice {
  id: string;
  label: string;
  device_token: string;
  expires_at: string;
}

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleDateString("de-CH") : "noch nie";
}

/** Parent-only card: approve this tablet for the children's login and manage approved devices. */
export default function DevicesCard() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [paired, setPaired] = useState(isDevicePaired());
  const [label, setLabel] = useState("Tablet");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  async function loadDevices() {
    try {
      setDevices(await apiFetch<Device[]>("/families/devices"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Geräte konnten nicht geladen werden");
    }
  }

  useEffect(() => {
    loadDevices();
  }, []);

  async function handlePair() {
    setError(null);
    setSuccess(null);
    setBusy(true);
    try {
      const result = await apiFetch<PairedDevice>("/families/devices", {
        method: "POST",
        body: JSON.stringify({ label: label.trim() || "Tablet" }),
      });
      setDeviceToken(result.device_token, result.id);
      const token = getToken();
      const familyId = token ? decodeFamilyIdFromToken(token) : null;
      if (familyId) setFamilyId(familyId);
      setPaired(true);
      setSuccess("Dieses Gerät ist jetzt für Kinder freigegeben. Kinder können sich hier mit ihrer PIN anmelden.");
      await loadDevices();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Freigabe fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  }

  async function handleRevoke(id: string) {
    setError(null);
    setSuccess(null);
    setBusy(true);
    try {
      await apiFetch<void>(`/families/devices/${id}`, { method: "DELETE" });
      if (id === getDeviceId()) {
        clearDeviceToken();
        setPaired(false);
      }
      await loadDevices();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Widerrufen fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  }

  async function handleUnpairThisDevice() {
    const id = getDeviceId();
    setError(null);
    setSuccess(null);
    setBusy(true);
    try {
      if (id) await apiFetch<void>(`/families/devices/${id}`, { method: "DELETE" });
    } catch {
      // Already revoked or unreachable: the local approval must go either way.
    }
    clearDeviceToken();
    setPaired(false);
    await loadDevices();
    setBusy(false);
  }

  const activeDevices = devices.filter((d) => !d.revoked);

  return (
    <div className="form-card">
      <h2 style={{ marginTop: 0, marginBottom: 12, fontSize: "18px" }}>Geräte</h2>
      <p style={{ fontSize: "0.9rem" }}>
        Aus Datenschutzgründen sehen nur Geräte, die du freigibst, die Kinder und das Wand-Dashboard. Auf allen
        anderen Geräten ist nur der Eltern-Login sichtbar.
      </p>

      {error && <div className="error-banner">{error}</div>}
      {success && <div className="dialog-notice" role="status">{success}</div>}

      {paired ? (
        <>
          <p>
            <strong>Dieses Gerät ist für Kinder freigegeben.</strong>
          </p>
          <button type="button" className="secondary" onClick={handleUnpairThisDevice} disabled={busy}>
            Freigabe für dieses Gerät aufheben
          </button>
        </>
      ) : (
        <>
          <p>Dieses Gerät ist nicht für Kinder freigegeben.</p>
          <label htmlFor="device-label">Bezeichnung dieses Geräts</label>
          <input
            id="device-label"
            type="text"
            value={label}
            maxLength={60}
            onChange={(e) => setLabel(e.target.value)}
          />
          <button type="button" onClick={handlePair} disabled={busy}>
            Dieses Gerät für Kinder freigeben
          </button>
        </>
      )}

      <h3 style={{ marginTop: 24, fontSize: "15px" }}>Freigegebene Geräte</h3>
      {activeDevices.length === 0 ? (
        <p className="empty-state">Noch kein Gerät freigegeben.</p>
      ) : (
        activeDevices.map((device) => (
          <div key={device.id} className="card" style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
            <div>
              <strong>{device.label}</strong>
              <div style={{ fontSize: "0.85rem", color: "var(--text-muted)" }}>
                Zuletzt benutzt: {formatDate(device.last_used_at)}
              </div>
            </div>
            <button type="button" className="secondary" onClick={() => handleRevoke(device.id)} disabled={busy}>
              Widerrufen
            </button>
          </div>
        ))
      )}
    </div>
  );
}
