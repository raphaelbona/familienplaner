import { useCallback, useEffect, useMemo, useState } from "react";
import {
  disconnectGoogle,
  fetchGoogleStatus,
  requestGoogleAuthorizationUrl,
  saveGoogleCalendars,
  syncGoogleNow,
  type GoogleSyncStatus,
} from "../api/googleSync";
import { fetchFamilyMembers, type FamilyMember } from "../api/members";

type Draft = Record<string, { selected: boolean; owners: string[] }>;
type Notice = { kind: "success" | "error"; text: string };

/** "gerade eben", "vor 12 Min.", "vor 3 Std.", "heute 08:15", otherwise date and time. */
export function formatLastSync(value: string | null, now: Date = new Date()): string {
  if (!value) return "noch nie";
  const then = new Date(value);
  const minutes = Math.floor((now.getTime() - then.getTime()) / 60000);
  if (minutes < 1) return "gerade eben";
  if (minutes < 60) return `vor ${minutes} Min.`;
  if (minutes < 6 * 60) return `vor ${Math.floor(minutes / 60)} Std.`;
  const time = then.toLocaleTimeString("de-CH", { hour: "2-digit", minute: "2-digit" });
  if (then.toDateString() === now.toDateString()) return `heute ${time}`;
  return then.toLocaleString("de-CH", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function toDraft(status: GoogleSyncStatus): Draft {
  return Object.fromEntries(status.calendars.map((c) => [c.id, { selected: c.selected, owners: [...c.owner_user_ids] }]));
}

function sameMembers(a: string[], b: string[]): boolean {
  return a.length === b.length && [...a].sort().join("|") === [...b].sort().join("|");
}

function errorText(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback;
}

type Props = {
  /** Called after an import, a saved selection or a disconnect changed the imported events. */
  onEventsChanged?: () => void;
  /** Where the browser goes for Google's consent screen (replaceable in tests). */
  onNavigate?: (url: string) => void;
};

/** Parent-only card: connect Google, pick calendars and who their events belong to, sync, disconnect. */
export default function GoogleSyncCard({
  onEventsChanged,
  onNavigate = (url) => {
    window.location.href = url;
  },
}: Props) {
  const [status, setStatus] = useState<GoogleSyncStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [members, setMembers] = useState<FamilyMember[]>([]);
  const [draft, setDraft] = useState<Draft>({});
  const [busy, setBusy] = useState<"" | "connect" | "sync" | "save" | "disconnect">("");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false);
  const [removeEvents, setRemoveEvents] = useState(false);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      setStatus(await fetchGoogleStatus());
    } catch (err) {
      setLoadError(errorText(err, "Google-Status konnte nicht geladen werden."));
    }
  }, []);

  useEffect(() => {
    load();
    fetchFamilyMembers()
      .then(setMembers)
      .catch(() => setMembers([]));
  }, [load]);

  useEffect(() => {
    if (status) setDraft(toDraft(status));
  }, [status]);

  const dirty = useMemo(() => {
    if (!status) return false;
    return status.calendars.some((c) => {
      const d = draft[c.id];
      if (!d) return false;
      return d.selected !== c.selected || (d.selected && !sameMembers(d.owners, c.owner_user_ids));
    });
  }, [status, draft]);

  async function handleConnect() {
    setBusy("connect");
    setNotice(null);
    try {
      onNavigate(await requestGoogleAuthorizationUrl());
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err, "Google-Kalender-Sync konnte nicht gestartet werden.") });
      setBusy("");
    }
  }

  async function handleSyncNow() {
    setBusy("sync");
    setNotice(null);
    try {
      setStatus(await syncGoogleNow());
      onEventsChanged?.();
    } catch (err) {
      // A 429 carries the German "Bitte kurz warten ..." message from the API.
      setNotice({ kind: "error", text: errorText(err, "Synchronisierung fehlgeschlagen.") });
    } finally {
      setBusy("");
    }
  }

  async function handleSave() {
    if (!status) return;
    setBusy("save");
    setNotice(null);
    try {
      const selection = status.calendars
        .filter((c) => draft[c.id]?.selected)
        .map((c) => ({ id: c.id, owner_user_ids: draft[c.id].owners }));
      setStatus(await saveGoogleCalendars(selection));
      setNotice({ kind: "success", text: "Gespeichert ✓" });
      // Import the new selection right away; a rate-limit or Google hiccup must not undo the saved selection.
      try {
        setStatus(await syncGoogleNow());
      } catch {
        // the worker picks it up within 15 minutes
      }
      onEventsChanged?.();
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err, "Speichern fehlgeschlagen.") });
    } finally {
      setBusy("");
    }
  }

  async function handleDisconnect() {
    setBusy("disconnect");
    setNotice(null);
    try {
      await disconnectGoogle(removeEvents);
      setConfirmingDisconnect(false);
      setRemoveEvents(false);
      await load();
      onEventsChanged?.();
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err, "Trennen fehlgeschlagen.") });
    } finally {
      setBusy("");
    }
  }

  function toggleCalendar(id: string) {
    setDraft((prev) => ({ ...prev, [id]: { ...prev[id], selected: !prev[id]?.selected, owners: prev[id]?.owners ?? [] } }));
  }

  function toggleMember(calendarId: string, memberId: string) {
    setDraft((prev) => {
      const current = prev[calendarId] ?? { selected: true, owners: [] };
      const owners = current.owners.includes(memberId)
        ? current.owners.filter((id) => id !== memberId)
        : [...current.owners, memberId];
      return { ...prev, [calendarId]: { ...current, owners } };
    });
  }

  const heading = (
    <div className="gsync-heading">
      <span className="icon-chip sky" aria-hidden="true">
        📅
      </span>
      <h3>Google Kalender</h3>
    </div>
  );

  if (!status) {
    return (
      <section className="gsync" aria-label="Google Kalender">
        {heading}
        {loadError ? (
          <>
            <div className="error-banner">{loadError}</div>
            <button type="button" className="secondary" onClick={load}>
              Erneut versuchen
            </button>
          </>
        ) : (
          <p className="card-meta">Wird geladen…</p>
        )}
      </section>
    );
  }

  if (!status.configured) {
    return (
      <section className="gsync" aria-label="Google Kalender">
        {heading}
        <p className="card-meta gsync-muted">
          <span aria-hidden="true">🔒</span> Google ist noch nicht eingerichtet
        </p>
      </section>
    );
  }

  const noticeBox = notice && (
    <div className={notice.kind === "error" ? "error-banner" : "dialog-notice"} role={notice.kind === "error" ? "alert" : "status"}>
      {notice.text}
    </div>
  );

  if (!status.connected) {
    return (
      <section className="gsync" aria-label="Google Kalender">
        {heading}
        {noticeBox}
        <button type="button" disabled={busy === "connect"} onClick={handleConnect}>
          <span aria-hidden="true">🔗</span> {busy === "connect" ? "Wird verbunden…" : "Mit Google verbinden"}
        </button>
      </section>
    );
  }

  const needsReauth = status.status === "needs_reauth";

  return (
    <section className="gsync" aria-label="Google Kalender">
      {heading}

      <div className="gsync-status">
        <div>
          <div className="gsync-email">{status.email ? `Verbunden als ${status.email}` : "Mit Google verbunden"}</div>
          <div className="card-meta">Zuletzt synchronisiert: {formatLastSync(status.last_synced_at)}</div>
        </div>
        {status.status === "ok" && (
          <span className="status-pill leaf">
            <span aria-hidden="true">✓</span> OK
          </span>
        )}
        {needsReauth && (
          <span className="status-pill accent">
            <span aria-hidden="true">⚠️</span> Neu verbinden nötig
          </span>
        )}
        {status.status === "error" && (
          <span className="status-pill accent">
            <span aria-hidden="true">⚠️</span> Fehler
          </span>
        )}
      </div>
      {status.status === "error" && status.last_error && <p className="card-meta gsync-error">{status.last_error}</p>}

      {noticeBox}

      <div className="gsync-actions">
        {needsReauth ? (
          <button type="button" disabled={busy === "connect"} onClick={handleConnect}>
            <span aria-hidden="true">🔗</span> Neu verbinden
          </button>
        ) : (
          <button type="button" disabled={busy !== ""} onClick={handleSyncNow}>
            <span aria-hidden="true">🔄</span> {busy === "sync" ? "Wird synchronisiert…" : "Jetzt synchronisieren"}
          </button>
        )}
        {!confirmingDisconnect && (
          <button type="button" className="secondary" disabled={busy !== ""} onClick={() => setConfirmingDisconnect(true)}>
            <span aria-hidden="true">✂️</span> Trennen
          </button>
        )}
      </div>

      {confirmingDisconnect && (
        <div className="gsync-confirm" role="group" aria-label="Google trennen">
          <label className="gsync-check">
            <input type="checkbox" checked={removeEvents} onChange={(e) => setRemoveEvents(e.target.checked)} />
            <span>Importierte Termine auch entfernen</span>
          </label>
          <div className="gsync-actions">
            <button type="button" disabled={busy !== ""} onClick={handleDisconnect}>
              Wirklich trennen
            </button>
            <button
              type="button"
              className="secondary"
              onClick={() => {
                setConfirmingDisconnect(false);
                setRemoveEvents(false);
              }}
            >
              Abbrechen
            </button>
          </div>
        </div>
      )}

      {status.calendars.length > 0 && (
        <>
          <h4 className="gsync-subtitle">Kalender</h4>
          <ul className="gsync-list">
            {status.calendars.map((calendar) => {
              const d = draft[calendar.id] ?? { selected: calendar.selected, owners: calendar.owner_user_ids };
              return (
                <li key={calendar.id} className="gsync-row" data-testid={`calendar-row-${calendar.id}`}>
                  <label className="gsync-check">
                    <input type="checkbox" checked={d.selected} onChange={() => toggleCalendar(calendar.id)} />
                    <span>{calendar.name}</span>
                  </label>
                  {d.selected && (
                    <div className="gsync-members">
                      {members.map((member) => {
                        const on = d.owners.includes(member.id);
                        return (
                          <button
                            key={member.id}
                            type="button"
                            className={`gsync-chip${on ? " on" : ""}`}
                            aria-pressed={on}
                            onClick={() => toggleMember(calendar.id, member.id)}
                          >
                            <span aria-hidden="true">{member.avatar_key ?? member.display_name.slice(0, 1).toUpperCase()}</span>{" "}
                            {member.display_name}
                          </button>
                        );
                      })}
                      {d.owners.length === 0 && <span className="gsync-family">Ganze Familie</span>}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
          <div className="gsync-actions">
            <button type="button" disabled={!dirty || busy !== ""} onClick={handleSave}>
              {busy === "save" ? "Wird gespeichert…" : "Speichern"}
            </button>
          </div>
        </>
      )}

      <p className="card-meta gsync-hint">
        <span aria-hidden="true">💡</span> Tipp: Mit &quot;Mia: Zahnarzt&quot; im Titel ordnest du einen Termin Mia zu. Termine nur für
        Eltern oder als privat markierte zeigen Kindern nur &quot;Belegt&quot;.
      </p>
      <p className="card-meta gsync-hint">
        <span aria-hidden="true">🏖️</span> Ganztägige Termine mit &quot;Ferien&quot; oder &quot;Schulfrei&quot; im Titel zählen als
        Ferien (auch &quot;Ferienwohnung&quot;); &quot;Mia: Ferien&quot; gilt nur für Mia.
      </p>
    </section>
  );
}
