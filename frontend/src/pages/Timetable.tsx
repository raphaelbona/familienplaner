import { FormEvent, useEffect, useState } from "react";
import { apiFetch, getToken, decodeRoleFromToken, decodeUserIdFromToken, getFamilyId } from "../api/client";
import SchoolBreaksCard from "../components/SchoolBreaksCard";

type TimetableEntry = {
  id: string;
  day_of_week: number;
  start_time: string;
  end_time: string;
  subject: string;
  room: string | null;
};

type ChildAccount = {
  id: string;
  display_name: string;
};

type Period = {
  start: string;
  end: string;
};

type CalendarEvent = {
  id: string;
  title: string;
  start: string;
  end: string;
  all_day: boolean;
  color: string | null;
  owner_user_id: string | null;
  source: string;
};

type TimetableDetail =
  | { type: "lesson"; entry: TimetableEntry; dayName: string }
  | { type: "event"; event: CalendarEvent };

const WEEKDAY_SHORT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
const WEEKDAY_LONG = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"];

function periodKey(period: Period): string {
  return `${period.start}-${period.end}`;
}

function slotKey(dayOfWeek: number, period: Period): string {
  return `${dayOfWeek}|${period.start}|${period.end}`;
}

function timeToMinutes(time: string): number {
  const [hours, minutes] = time.split(":").map(Number);
  return hours * 60 + minutes;
}

function mondayOfCurrentWeek(): Date {
  const date = new Date();
  const day = date.getDay() || 7;
  date.setHours(0, 0, 0, 0);
  date.setDate(date.getDate() - day + 1);
  return date;
}

function subjectColorIndex(subject: string): number {
  return Array.from(subject).reduce((sum, character) => sum + character.charCodeAt(0), 0) % 8;
}

function eventTimeLabel(event: CalendarEvent): string {
  if (event.all_day) return "Ganztägig";
  const start = new Date(event.start);
  const end = new Date(event.end);
  return `${start.toLocaleTimeString("de-CH", { hour: "2-digit", minute: "2-digit" })}–${end.toLocaleTimeString("de-CH", { hour: "2-digit", minute: "2-digit" })}`;
}

function eventsForDate(events: CalendarEvent[], date: Date): CalendarEvent[] {
  return events.filter((event) => {
    const eventDate = new Date(event.start);
    return (
      eventDate.getFullYear() === date.getFullYear() &&
      eventDate.getMonth() === date.getMonth() &&
      eventDate.getDate() === date.getDate()
    );
  });
}

// Gaps of at least this many minutes between two back-to-back periods are shown as a
// break row (e.g. lunch), so they aren't just an unexplained hole in the grid. Shorter
// gaps (a few minutes to walk to the next room) are left alone.
const BREAK_THRESHOLD_MINUTES = 30;

type GridRow =
  | { type: "period"; period: Period }
  | { type: "break"; start: string; end: string; label: string };

function buildGridRows(periods: Period[]): GridRow[] {
  const rows: GridRow[] = [];
  periods.forEach((period, index) => {
    rows.push({ type: "period", period });

    const next = periods[index + 1];
    if (!next) return;

    const gap = timeToMinutes(next.start) - timeToMinutes(period.end);
    if (gap >= BREAK_THRESHOLD_MINUTES) {
      const breakStartMinutes = timeToMinutes(period.end);
      const isLunchWindow = breakStartMinutes >= 11 * 60 && breakStartMinutes <= 14 * 60 + 30;
      rows.push({
        type: "break",
        start: period.end,
        end: next.start,
        label: isLunchWindow ? "🍽️ Mittagspause" : "☕ Pause",
      });
    }
  });
  return rows;
}

function ReadOnlyWeekTable({ child, entries, events }: { child: ChildAccount; entries: TimetableEntry[]; events: CalendarEvent[] }) {
  const [detail, setDetail] = useState<TimetableDetail | null>(null);
  const periodMap = new Map<string, Period>();
  entries.forEach((entry) => {
    const period = { start: entry.start_time, end: entry.end_time };
    periodMap.set(periodKey(period), period);
  });
  const periods = Array.from(periodMap.values()).sort((a, b) => timeToMinutes(a.start) - timeToMinutes(b.start));
  const gridRows = buildGridRows(periods);
  const weekStart = mondayOfCurrentWeek();
  const weekDates = WEEKDAY_SHORT.map((_, index) => {
    const date = new Date(weekStart);
    date.setDate(weekStart.getDate() + index);
    return date;
  });
  const childEvents = events.filter((event) => event.owner_user_id === child.id && event.source !== "timetable");
  const hasWeekEvents = weekDates.some((date) => eventsForDate(childEvents, date).length > 0);

  function findEntry(dayOfWeek: number, period: Period): TimetableEntry | undefined {
    return entries.find(
      (entry) => entry.day_of_week === dayOfWeek && entry.start_time === period.start && entry.end_time === period.end
    );
  }

  return (
    <section className="form-card timetable-family-section" aria-label={`Stundenplan von ${child.display_name}`}>
      <div className="timetable-child-heading">
        <span className="task-child-avatar" aria-hidden="true">🎒</span>
        <div>
          <h2>{child.display_name}</h2>
          <div className="card-meta">Schule und Termine dieser Woche</div>
        </div>
      </div>

      {periods.length === 0 && !hasWeekEvents ? (
        <div className="empty-state">Noch kein Stundenplan vorhanden.</div>
      ) : (
        <div className="timetable-scroll">
          <table className="timetable-grid">
            <thead>
              <tr>
                <th className="timetable-time-heading">Zeit</th>
                {WEEKDAY_SHORT.map((name, dayIndex) => (
                  <th key={name} className={weekDates[dayIndex].toDateString() === new Date().toDateString() ? "is-today" : ""}>
                    <span>{name}</span>
                    <small>{weekDates[dayIndex].toLocaleDateString("de-CH", { day: "2-digit", month: "2-digit" })}</small>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr className="timetable-events-row">
                <th scope="row"><span aria-hidden="true">📌</span>Termine</th>
                {weekDates.map((date) => {
                  const dayEvents = eventsForDate(childEvents, date);
                  return (
                    <td key={date.toISOString()}>
                      {dayEvents.length === 0 ? <span className="timetable-empty">–</span> : (
                        <div className="timetable-event-list">
                          {dayEvents.map((event) => (
                            <button type="button" key={event.id} className="timetable-event" onClick={() => setDetail({ type: "event", event })}>
                              <span aria-hidden="true">📅</span><span>{event.title}</span><small>{eventTimeLabel(event)}</small>
                            </button>
                          ))}
                        </div>
                      )}
                    </td>
                  );
                })}
              </tr>
              {gridRows.map((row) => {
                if (row.type === "break") {
                  return <tr key={`break-${row.start}-${row.end}`} className="timetable-break-row"><td colSpan={8}>{row.label} · {row.start}–{row.end}</td></tr>;
                }
                return (
                  <tr key={periodKey(row.period)}>
                    <th scope="row" className="timetable-time-cell"><strong>{row.period.start}</strong><span>{row.period.end}</span></th>
                    {WEEKDAY_SHORT.map((_, dayIndex) => {
                      const entry = findEntry(dayIndex, row.period);
                      return (
                        <td key={dayIndex}>
                          {entry ? (
                            <div className="timetable-lesson-wrap">
                              <button type="button" className={`timetable-lesson subject-color-${subjectColorIndex(entry.subject)}`} onClick={() => setDetail({ type: "lesson", entry, dayName: WEEKDAY_LONG[dayIndex] })}>
                                <strong>{entry.subject}</strong>
                                {entry.room && <small>📍 {entry.room}</small>}
                                <span className="timetable-more">Details ansehen</span>
                              </button>
                            </div>
                          ) : <span className="timetable-empty">–</span>}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {detail && (
        <div className="timetable-detail" role="status" aria-live="polite">
          <div className="timetable-detail-icon" aria-hidden="true">{detail.type === "lesson" ? "🎒" : "📅"}</div>
          <div>
            <div className="timetable-detail-kicker">{child.display_name} · {detail.type === "lesson" ? "Schulstunde" : "Termin"}</div>
            <div className="timetable-detail-title">{detail.type === "lesson" ? detail.entry.subject : detail.event.title}</div>
            <div className="timetable-detail-meta">
              {detail.type === "lesson"
                ? `${detail.dayName}, ${detail.entry.start_time}–${detail.entry.end_time}${detail.entry.room ? ` · Raum ${detail.entry.room}` : ""}`
                : `${new Date(detail.event.start).toLocaleDateString("de-CH", { weekday: "long", day: "2-digit", month: "long" })} · ${eventTimeLabel(detail.event)}`}
            </div>
          </div>
          <button type="button" className="timetable-detail-close" onClick={() => setDetail(null)} aria-label="Details schließen">✕</button>
        </div>
      )}
    </section>
  );
}

export default function TimetablePage() {
  const [entries, setEntries] = useState<TimetableEntry[]>([]);
  const [calendarEvents, setCalendarEvents] = useState<CalendarEvent[]>([]);
  const [detail, setDetail] = useState<TimetableDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isParent, setIsParent] = useState(false);
  const [isDashboard, setIsDashboard] = useState(false);
  const [dashboardSchedules, setDashboardSchedules] = useState<Record<string, TimetableEntry[]>>({});

  // Parent UI state
  const [childAccounts, setChildAccounts] = useState<ChildAccount[]>([]);
  const [selectedChild, setSelectedChild] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);

  // Weekly-grid manual entry state
  const [customPeriods, setCustomPeriods] = useState<Period[]>([]);
  const [newPeriodStart, setNewPeriodStart] = useState("");
  const [newPeriodEnd, setNewPeriodEnd] = useState("");
  const [selectedCells, setSelectedCells] = useState<Set<string>>(new Set());
  const [assignSubject, setAssignSubject] = useState("");
  const [assignRoom, setAssignRoom] = useState("");
  const [assigning, setAssigning] = useState(false);
  const [deletingEntryId, setDeletingEntryId] = useState<string | null>(null);

  function loadChildren(loadAllSchedules = false) {
    const familyId = getFamilyId();
    if (!familyId) return;

    apiFetch<ChildAccount[]>(`/families/${familyId}/child-accounts`)
      .then(async (children) => {
        setChildAccounts(children);
        if (loadAllSchedules) {
          const schedules = await Promise.all(
            children.map(async (child) => [child.id, await apiFetch<TimetableEntry[]>(`/timetables/${child.id}`)] as const)
          );
          setDashboardSchedules(Object.fromEntries(schedules));
        }
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Kinder konnten nicht geladen werden"));
  }

  function loadTimetable(childId: string) {
    if (!childId) {
      setEntries([]);
      return;
    }

    apiFetch<TimetableEntry[]>(`/timetables/${childId}`)
      .then(setEntries)
      .catch((err) => setError(err instanceof Error ? err.message : "Stundenplan konnte nicht geladen werden"));
  }

  function loadCalendarEvents() {
    apiFetch<CalendarEvent[]>("/calendar/events")
      .then(setCalendarEvents)
      .catch((err) => setError(err instanceof Error ? err.message : "Termine konnten nicht geladen werden"));
  }

  useEffect(() => {
    const token = getToken();
    const role = decodeRoleFromToken(token || "");
    const isParentRole = role === "parent_admin" || role === "parent_member";
    const isDashboardRole = role === "dashboard";
    setIsParent(isParentRole);
    setIsDashboard(isDashboardRole);
    loadCalendarEvents();

    if (isParentRole || isDashboardRole) {
      loadChildren(isDashboardRole);
    } else {
      // Children view their own timetable directly, without a selector.
      const ownUserId = token ? decodeUserIdFromToken(token) : null;
      if (ownUserId) {
        setSelectedChild(ownUserId);
        loadTimetable(ownUserId);
      }
    }
  }, []);

  function resetGridState() {
    setCustomPeriods([]);
    setSelectedCells(new Set());
    setAssignSubject("");
    setAssignRoom("");
  }

  async function handleUpload(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();

    if (!selectedChild || !selectedFile) {
      setError("Bitte wählen Sie ein Kind und eine Datei aus");
      return;
    }

    setUploading(true);
    setError(null);

    try {
      const formData = new FormData();
      formData.append("child_user_id", selectedChild);
      formData.append("file", selectedFile);

      const uploadedEntries = await apiFetch<TimetableEntry[]>("/timetables/upload", {
        method: "POST",
        body: formData,
      });
      setEntries(uploadedEntries);
      setSelectedFile(null);
      setError(null);
      resetGridState();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload fehlgeschlagen");
    } finally {
      setUploading(false);
    }
  }

  function handleChildChange(childId: string) {
    setSelectedChild(childId);
    setDetail(null);
    resetGridState();
    loadTimetable(childId);
  }

  function handleAddPeriod(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!newPeriodStart || !newPeriodEnd) return;

    setCustomPeriods((prev) => {
      if (prev.some((p) => p.start === newPeriodStart && p.end === newPeriodEnd)) return prev;
      return [...prev, { start: newPeriodStart, end: newPeriodEnd }];
    });
    setNewPeriodStart("");
    setNewPeriodEnd("");
  }

  function toggleCell(dayOfWeek: number, period: Period) {
    const key = slotKey(dayOfWeek, period);
    setSelectedCells((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }

  async function handleBulkAssign() {
    if (!selectedChild || selectedCells.size === 0 || !assignSubject.trim()) {
      setError("Bitte ein Fach eingeben und mindestens eine Stunde auswählen");
      return;
    }

    setAssigning(true);
    setError(null);

    try {
      const slots = Array.from(selectedCells).map((key) => {
        const [dayOfWeek, start, end] = key.split("|");
        return { day_of_week: Number(dayOfWeek), start_time: start, end_time: end };
      });

      await apiFetch<TimetableEntry[]>("/timetables/bulk-assign", {
        method: "POST",
        body: JSON.stringify({
          child_user_id: selectedChild,
          subject: assignSubject.trim(),
          room: assignRoom.trim() || null,
          slots,
        }),
      });

      setSelectedCells(new Set());
      setAssignSubject("");
      setAssignRoom("");
      loadTimetable(selectedChild);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Zuweisung fehlgeschlagen");
    } finally {
      setAssigning(false);
    }
  }

  async function handleDeleteEntry(entryId: string) {
    setDeletingEntryId(entryId);
    setError(null);

    try {
      await apiFetch(`/timetables/${entryId}`, { method: "DELETE" });
      loadTimetable(selectedChild);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Eintrag konnte nicht gelöscht werden");
    } finally {
      setDeletingEntryId(null);
    }
  }

  // Periods (grid rows) are the union of times already used by entries plus any the parent
  // has added but not yet assigned a subject to.
  const periodMap = new Map<string, Period>();
  for (const period of customPeriods) periodMap.set(periodKey(period), period);
  for (const entry of entries) {
    const period = { start: entry.start_time, end: entry.end_time };
    periodMap.set(periodKey(period), period);
  }
  const periods = Array.from(periodMap.values()).sort(
    (a, b) => timeToMinutes(a.start) - timeToMinutes(b.start)
  );
  const gridRows = buildGridRows(periods);
  const weekStart = mondayOfCurrentWeek();
  const weekDates = WEEKDAY_SHORT.map((_, index) => {
    const date = new Date(weekStart);
    date.setDate(weekStart.getDate() + index);
    return date;
  });
  const childEvents = calendarEvents.filter(
    (event) => event.owner_user_id === selectedChild && event.source !== "timetable"
  );
  const weekEventCount = weekDates.reduce((total, date) => total + eventsForDate(childEvents, date).length, 0);

  function eventsForDay(date: Date): CalendarEvent[] {
    return eventsForDate(childEvents, date);
  }

  function findEntry(dayOfWeek: number, period: Period): TimetableEntry | undefined {
    return entries.find(
      (entry) => entry.day_of_week === dayOfWeek && entry.start_time === period.start && entry.end_time === period.end
    );
  }

  return (
    <>
      <h1>Stundenplan</h1>
      <p className="subtitle">Digitalisierte Stundenpläne für die ganze Familie.</p>

      {error && <div className="error-banner">{error}</div>}

      <SchoolBreaksCard canEdit={isParent} />

      {isParent && (
        <div className="form-card">
          <h2 style={{ marginTop: 0 }}>Stundenplan hochladen</h2>

          <form onSubmit={handleUpload}>
            <label>
              <span>Kind</span>
              <select
                value={selectedChild}
                onChange={(e) => handleChildChange(e.target.value)}
                disabled={uploading}
                required
              >
                <option value="">-- Kind auswählen --</option>
                {childAccounts.map((child) => (
                  <option key={child.id} value={child.id}>
                    {child.display_name}
                  </option>
                ))}
              </select>
            </label>

            <label>
              <span>Bild hochladen</span>
              <input
                type="file"
                accept="image/*"
                onChange={(e) => setSelectedFile(e.target.files?.[0] || null)}
                disabled={uploading}
                required
              />
            </label>

            <button type="submit" disabled={uploading || !selectedChild || !selectedFile}>
              {uploading ? "Lädt..." : "Hochladen"}
            </button>
          </form>
        </div>
      )}

      {isDashboard && (
        <div className="timetable-family-overview">
          <div className="timetable-overview-heading">
            <div>
              <h2>Alle Kinder im Überblick</h2>
              <p>Unterricht und persönliche Termine für diese Woche.</p>
            </div>
            <span aria-hidden="true">🏫</span>
          </div>
          {childAccounts.length === 0 ? (
            <div className="empty-state-playful">
              <span className="emoji" aria-hidden="true">📚</span>
              <div className="headline">Noch keine Stundenpläne</div>
            </div>
          ) : (
            childAccounts.map((child) => (
              <ReadOnlyWeekTable
                key={child.id}
                child={child}
                entries={dashboardSchedules[child.id] || []}
                events={calendarEvents}
              />
            ))
          )}
        </div>
      )}

      {selectedChild && (
        <div className="form-card">
          <h2 style={{ marginTop: 0 }}>Wochenplan</h2>
          {isParent && (
            <p className="subtitle" style={{ marginTop: 0 }}>
              Stunden im Kalenderblatt auswählen (mehrfach möglich) und unten ein Fach zuweisen — oder
              zuerst ein Fach eintragen und dann die passenden Stunden anklicken.
            </p>
          )}

          {periods.length === 0 && weekEventCount === 0 ? (
            <p className="empty-state">
              {isParent ? "Noch keine Lektionen erfasst. Zuerst unten eine Lektionszeit hinzufügen." : "Noch kein Stundenplan vorhanden."}
            </p>
          ) : (
            <div className="timetable-scroll">
              <table className="timetable-grid">
                <thead>
                  <tr>
                    <th className="timetable-time-heading">Zeit</th>
                    {WEEKDAY_SHORT.map((name, dayIndex) => (
                      <th key={name} className={weekDates[dayIndex].toDateString() === new Date().toDateString() ? "is-today" : ""}>
                        <span>{name}</span>
                        <small>{weekDates[dayIndex].toLocaleDateString("de-CH", { day: "2-digit", month: "2-digit" })}</small>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <tr className="timetable-events-row">
                    <th scope="row">
                      <span aria-hidden="true">📌</span>
                      Termine
                    </th>
                    {weekDates.map((date) => {
                      const dayEvents = eventsForDay(date);
                      return (
                        <td key={date.toISOString()}>
                          {dayEvents.length === 0 ? (
                            <span className="timetable-empty">–</span>
                          ) : (
                            <div className="timetable-event-list">
                              {dayEvents.map((event) => (
                                <button
                                  type="button"
                                  key={event.id}
                                  className="timetable-event"
                                  onClick={() => setDetail({ type: "event", event })}
                                >
                                  <span aria-hidden="true">📅</span>
                                  <span>{event.title}</span>
                                  <small>{eventTimeLabel(event)}</small>
                                </button>
                              ))}
                            </div>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                  {gridRows.map((row) => {
                    if (row.type === "break") {
                      return (
                        <tr key={`break-${row.start}-${row.end}`} className="timetable-break-row">
                          <td colSpan={WEEKDAY_SHORT.length + 1}>
                            {row.label} · {row.start}–{row.end}
                          </td>
                        </tr>
                      );
                    }

                    const period = row.period;
                    return (
                      <tr key={periodKey(period)}>
                        <th scope="row" className="timetable-time-cell">
                          <strong>{period.start}</strong>
                          <span>{period.end}</span>
                        </th>
                        {WEEKDAY_SHORT.map((_, dayIndex) => {
                          const entry = findEntry(dayIndex, period);
                          const key = slotKey(dayIndex, period);
                          const isSelected = selectedCells.has(key);

                          return (
                            <td
                              key={dayIndex}
                              onClick={isParent ? () => toggleCell(dayIndex, period) : undefined}
                              className={`${isParent ? "is-editable" : ""} ${isSelected ? "is-selected" : ""}`}
                            >
                              {entry ? (
                                <div className="timetable-lesson-wrap">
                                  <button
                                    type="button"
                                    className={`timetable-lesson subject-color-${subjectColorIndex(entry.subject)}`}
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      setDetail({ type: "lesson", entry, dayName: WEEKDAY_LONG[dayIndex] });
                                    }}
                                  >
                                    <strong>{entry.subject}</strong>
                                    {entry.room && <small>📍 {entry.room}</small>}
                                    <span className="timetable-more">Details ansehen</span>
                                  </button>
                                  {isParent && (
                                    <button
                                      type="button"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        handleDeleteEntry(entry.id);
                                      }}
                                      disabled={deletingEntryId === entry.id}
                                      aria-label={`${entry.subject} löschen`}
                                      className="timetable-delete"
                                    >
                                      {deletingEntryId === entry.id ? "…" : "✕"}
                                    </button>
                                  )}
                                </div>
                              ) : (
                                <span className="timetable-empty">{isParent ? "+" : "–"}</span>
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {detail && (
            <div className="timetable-detail" role="status" aria-live="polite">
              <div className="timetable-detail-icon" aria-hidden="true">{detail.type === "lesson" ? "🎒" : "📅"}</div>
              <div>
                <div className="timetable-detail-kicker">{detail.type === "lesson" ? "Schulstunde" : "Termin"}</div>
                <div className="timetable-detail-title">
                  {detail.type === "lesson" ? detail.entry.subject : detail.event.title}
                </div>
                <div className="timetable-detail-meta">
                  {detail.type === "lesson"
                    ? `${detail.dayName}, ${detail.entry.start_time}–${detail.entry.end_time}${detail.entry.room ? ` · Raum ${detail.entry.room}` : ""}`
                    : `${new Date(detail.event.start).toLocaleDateString("de-CH", { weekday: "long", day: "2-digit", month: "long" })} · ${eventTimeLabel(detail.event)}`}
                </div>
              </div>
              <button type="button" className="timetable-detail-close" onClick={() => setDetail(null)} aria-label="Details schließen">✕</button>
            </div>
          )}

          {isParent && (
            <>
              <form onSubmit={handleAddPeriod} style={{ marginTop: "16px", display: "flex", gap: "12px", alignItems: "flex-end" }}>
                <label style={{ flex: 1 }}>
                  <span>Neue Lektion — von</span>
                  <input type="time" value={newPeriodStart} onChange={(e) => setNewPeriodStart(e.target.value)} />
                </label>
                <label style={{ flex: 1 }}>
                  <span>bis</span>
                  <input type="time" value={newPeriodEnd} onChange={(e) => setNewPeriodEnd(e.target.value)} />
                </label>
                <button type="submit" disabled={!newPeriodStart || !newPeriodEnd}>
                  + Lektion
                </button>
              </form>

              <div
                style={{
                  marginTop: "20px",
                  paddingTop: "16px",
                  borderTop: "1px solid var(--border)",
                  display: "flex",
                  gap: "12px",
                  alignItems: "flex-end",
                  flexWrap: "wrap",
                }}
              >
                <label style={{ flex: 1, minWidth: "160px" }}>
                  <span>Fach</span>
                  <input
                    type="text"
                    value={assignSubject}
                    onChange={(e) => setAssignSubject(e.target.value)}
                    placeholder="z. B. Mathematik"
                  />
                </label>
                <label style={{ flex: 1, minWidth: "140px" }}>
                  <span>Raum (optional)</span>
                  <input
                    type="text"
                    value={assignRoom}
                    onChange={(e) => setAssignRoom(e.target.value)}
                    placeholder="z. B. G 1.05"
                  />
                </label>
                <button
                  type="button"
                  onClick={handleBulkAssign}
                  disabled={assigning || selectedCells.size === 0 || !assignSubject.trim()}
                >
                  {assigning ? "Wird gespeichert…" : `${selectedCells.size} Stunde(n) zuweisen`}
                </button>
                {selectedCells.size > 0 && (
                  <button type="button" onClick={() => setSelectedCells(new Set())} disabled={assigning}>
                    Auswahl leeren
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </>
  );
}
