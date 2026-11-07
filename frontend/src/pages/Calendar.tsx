import { FormEvent, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { apiFetch, decodeRoleFromToken, getFamilyId, getToken } from "../api/client";
import GoogleSyncCard from "../components/GoogleSyncCard";
import PasswordInput from "../components/PasswordInput";

type CalendarEvent = {
  id: string;
  title: string;
  start: string;
  end: string;
  all_day: boolean;
  source: string;
  owner_user_id: string | null;
  recurrence_rule: "daily" | "weekly" | null;
};

type ChildAccount = {
  id: string;
  display_name: string;
};

export default function Calendar() {
  const [searchParams, setSearchParams] = useSearchParams();
  const role = decodeRoleFromToken(getToken() ?? "");
  const isParent = role === "parent_admin" || role === "parent_member";

  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [childAccounts, setChildAccounts] = useState<ChildAccount[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [selectedChildIds, setSelectedChildIds] = useState<string[]>([]);
  const [recurrenceRule, setRecurrenceRule] = useState<"" | "daily" | "weekly">("");

  const [syncNotice, setSyncNotice] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const [caldavUrl, setCaldavUrl] = useState("");
  const [caldavUsername, setCaldavUsername] = useState("");
  const [caldavPassword, setCaldavPassword] = useState("");
  const [caldavConnecting, setCaldavConnecting] = useState(false);
  const [caldavError, setCaldavError] = useState<string | null>(null);

  function loadEvents() {
    apiFetch<CalendarEvent[]>("/calendar/events")
      .then(setEvents)
      .catch((err) => setError(err instanceof Error ? err.message : "Termine konnten nicht geladen werden"));
  }

  useEffect(loadEvents, []);

  useEffect(() => {
    if (!isParent) return;
    const familyId = getFamilyId();
    if (!familyId) return;
    apiFetch<ChildAccount[]>(`/families/${familyId}/child-accounts`)
      .then(setChildAccounts)
      .catch(() => setChildAccounts([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function toggleChild(childId: string) {
    setSelectedChildIds((prev) =>
      prev.includes(childId) ? prev.filter((id) => id !== childId) : [...prev, childId]
    );
  }

  // Show a banner after returning from the Google OAuth redirect, then clear the query params.
  useEffect(() => {
    const googleStatus = searchParams.get("google");
    if (!googleStatus) return;

    if (googleStatus === "connected") {
      setSyncNotice({ kind: "success", text: "Google-Kalender erfolgreich verbunden." });
    } else if (googleStatus === "error") {
      setSyncNotice({
        kind: "error",
        text: searchParams.get("message") || "Google-Kalender konnte nicht verbunden werden."
      });
    }
    setSearchParams({}, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleConnectCaldav(event: FormEvent) {
    event.preventDefault();
    setCaldavError(null);
    setCaldavConnecting(true);
    try {
      await apiFetch("/calendar/sync/caldav", {
        method: "POST",
        body: JSON.stringify({ server_url: caldavUrl, username: caldavUsername, password: caldavPassword })
      });
      setSyncNotice({ kind: "success", text: "CalDAV-Kalender erfolgreich verbunden." });
      setCaldavUrl("");
      setCaldavUsername("");
      setCaldavPassword("");
    } catch (err) {
      setCaldavError(err instanceof Error ? err.message : "CalDAV-Kalender konnte nicht verbunden werden.");
    } finally {
      setCaldavConnecting(false);
    }
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await apiFetch("/calendar/events", {
        method: "POST",
        body: JSON.stringify({
          title,
          // datetime-local values have no timezone; treat them as local wall-clock
          // time and convert to an explicit UTC instant before sending, so the
          // stored time round-trips back to the same local time on display.
          start: new Date(start).toISOString(),
          end: new Date(end).toISOString(),
          all_day: false,
          owner_user_ids: selectedChildIds.length > 0 ? selectedChildIds : undefined,
          recurrence_rule: recurrenceRule || null
        })
      });
      setTitle("");
      setStart("");
      setEnd("");
      setSelectedChildIds([]);
      setRecurrenceRule("");
      loadEvents();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Termin konnte nicht angelegt werden");
    }
  }

  return (
    <>
      <h1>Familienkalender</h1>
      <p className="subtitle">Gemeinsame Termine, optional synchronisiert mit Google oder CalDAV.</p>

      {error && <div className="error-banner">{error}</div>}
      {syncNotice && (
        <div className={syncNotice.kind === "error" ? "error-banner" : "card"} style={{ marginBottom: 16 }}>
          {syncNotice.text}
        </div>
      )}

      {isParent && (
        <div className="form-card" style={{ maxWidth: "none", marginBottom: 22 }}>
          <h2 style={{ marginTop: 0, marginBottom: 16, fontSize: "16px" }}>Kalender-Sync</h2>

          <GoogleSyncCard onEventsChanged={loadEvents} />

          <p className="card-meta" style={{ margin: "16px 0 8px 0" }}>
            Oder CalDAV (iCloud, Nextcloud, selbst gehostet):
          </p>
          {caldavError && <div className="error-banner">{caldavError}</div>}
          <form onSubmit={handleConnectCaldav}>
            <label htmlFor="caldav-url">Server-URL</label>
            <input
              id="caldav-url"
              type="url"
              value={caldavUrl}
              onChange={(e) => setCaldavUrl(e.target.value)}
              placeholder="https://cloud.example.com/remote.php/dav"
              required
            />
            <label htmlFor="caldav-username">Benutzername</label>
            <input
              id="caldav-username"
              value={caldavUsername}
              onChange={(e) => setCaldavUsername(e.target.value)}
              required
            />
            <label htmlFor="caldav-password">Passwort</label>
            <PasswordInput
              id="caldav-password"
              autoComplete="off"
              value={caldavPassword}
              onChange={(e) => setCaldavPassword(e.target.value)}
              required
            />
            <button type="submit" disabled={caldavConnecting}>
              {caldavConnecting ? "Wird verbunden…" : "CalDAV verbinden"}
            </button>
          </form>
        </div>
      )}

      {isParent && (
        <form onSubmit={handleSubmit} className="form-card" style={{ maxWidth: "none", marginBottom: 22 }}>
          <label htmlFor="title">Titel</label>
          <input id="title" value={title} onChange={(e) => setTitle(e.target.value)} required />
          <label htmlFor="start">Beginn</label>
          <input id="start" type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} required />
          <label htmlFor="end">Ende</label>
          <input id="end" type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} required />

          <label htmlFor="recurrence">Wiederholung</label>
          <select
            id="recurrence"
            value={recurrenceRule}
            onChange={(e) => setRecurrenceRule(e.target.value as "" | "daily" | "weekly")}
          >
            <option value="">Einmalig</option>
            <option value="daily">Täglich</option>
            <option value="weekly">Wöchentlich</option>
          </select>

          {childAccounts.length > 0 && (
            <>
              <label>Zugewiesen an (optional, sonst ganze Familie)</label>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
                {childAccounts.map((child) => {
                  const checked = selectedChildIds.includes(child.id);
                  return (
                    <label
                      key={child.id}
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 6,
                        margin: 0,
                        padding: "8px 14px",
                        borderRadius: 999,
                        border: `1.5px solid ${checked ? "var(--accent)" : "var(--border)"}`,
                        background: checked ? "var(--accent-soft)" : "var(--surface)",
                        color: checked ? "var(--accent)" : "var(--text)",
                        cursor: "pointer",
                        fontSize: 14,
                        fontWeight: 500,
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleChild(child.id)}
                        style={{ width: "auto", margin: 0 }}
                      />
                      {child.display_name}
                    </label>
                  );
                })}
              </div>
            </>
          )}

          <button type="submit">Termin hinzufügen</button>
        </form>
      )}

      {events.length === 0 ? (
        <p className="empty-state">Noch keine Termine.</p>
      ) : (
        events.map((event) => {
          const assignee = childAccounts.find((c) => c.id === event.owner_user_id);
          return (
            <div key={event.id} className="card">
              <div className="icon-chip sky" aria-hidden="true">
                📅
              </div>
              <div className="card-body">
                <div className="card-title">{event.title}</div>
                <div className="card-meta">
                  {new Date(event.start).toLocaleString("de-CH")} – {new Date(event.end).toLocaleString("de-CH")}
                </div>
                {(assignee || event.recurrence_rule || event.source === "google") && (
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
                    {assignee && <span className="status-pill accent">{assignee.display_name}</span>}
                    {event.source === "google" && (
                      <span className="status-pill sky" title="Aus Google Kalender importiert">
                        <span aria-hidden="true">📅</span> Google
                      </span>
                    )}
                    {event.recurrence_rule === "daily" && <span className="status-pill">Täglich</span>}
                    {event.recurrence_rule === "weekly" && <span className="status-pill">Wöchentlich</span>}
                  </div>
                )}
              </div>
            </div>
          );
        })
      )}
    </>
  );
}
