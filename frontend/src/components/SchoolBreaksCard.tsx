import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  createSchoolBreak,
  deleteSchoolBreak,
  fetchSchoolBreaks,
  updateSchoolBreak,
  type SchoolBreak,
  type SchoolBreakKind,
} from "../api/schoolBreaks";
import { apiFetch } from "../api/client";
import { AVATAR_OPTIONS } from "../constants/avatars";
import { localIsoDateOffset, todayIsoDate } from "../utils/date";
import { BREAK_KIND_LABELS } from "../utils/dayBar";
import { colorForPerson } from "../utils/personColor";
import { formatBreakRange, sortBreaks } from "../utils/schoolBreaks";
import { BeachIcon, SchoolOffIcon } from "./dayIcons";

type Child = { id: string; display_name: string; avatar_key: string | null; color_index?: number };

type Props = {
  /** Parents may add, change and delete; everyone else only reads. */
  canEdit: boolean;
};

const EMPTY_FORM = { title: "", kind: "ferien" as SchoolBreakKind, start: "", end: "", childIds: [] as string[] };

function errorText(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback;
}

function KindIcon({ kind, size = 22 }: { kind: SchoolBreakKind; size?: number }) {
  return kind === "ferien" ? <BeachIcon size={size} /> : <SchoolOffIcon size={size} />;
}

/** "Ferien und freie Tage": school holidays and free days per child (timetable page). */
export default function SchoolBreaksCard({ canEdit }: Props) {
  const [breaks, setBreaks] = useState<SchoolBreak[]>([]);
  const [children, setChildren] = useState<Child[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const [formOpen, setFormOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const load = useCallback(() => {
    // A year ahead and a month back: enough to plan the next holidays and to see the last ones greyed out.
    return fetchSchoolBreaks(localIsoDateOffset(-30), localIsoDateOffset(365))
      .then((rows) => {
        setBreaks(Array.isArray(rows) ? rows : []);
        setError(null);
      })
      .catch((err) => setError(errorText(err, "Ferien konnten nicht geladen werden")))
      .finally(() => setLoaded(true));
  }, []);

  useEffect(() => {
    load();
    apiFetch<Child[]>("/families/children")
      .then((rows) => setChildren(Array.isArray(rows) ? rows : []))
      .catch(() => setChildren([]));
  }, [load]);

  function childById(id: string): Child | undefined {
    return children.find((c) => c.id === id);
  }

  function colorOf(child: Child): string {
    return colorForPerson(child.color_index ?? children.indexOf(child));
  }

  function openNew() {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setFormError(null);
    setConfirmDeleteId(null);
    setFormOpen(true);
  }

  function openEdit(brk: SchoolBreak) {
    setEditingId(brk.id);
    setForm({ title: brk.title, kind: brk.kind, start: brk.start_date, end: brk.end_date, childIds: [...brk.child_user_ids] });
    setFormError(null);
    setConfirmDeleteId(null);
    setFormOpen(true);
  }

  function closeForm() {
    setFormOpen(false);
    setEditingId(null);
    setFormError(null);
  }

  function setStart(value: string) {
    // "Bis" follows "Von" until the parent picks a later end on purpose (single days need no second pick).
    setForm((f) => ({ ...f, start: value, end: !f.end || f.end < value || f.end === f.start ? value : f.end }));
  }

  function toggleChild(id: string) {
    setForm((f) => ({
      ...f,
      childIds: f.childIds.includes(id) ? f.childIds.filter((c) => c !== id) : [...f.childIds, id],
    }));
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const title = form.title.trim();
    if (!title) return setFormError("Bitte einen Namen eingeben.");
    if (!form.start || !form.end) return setFormError("Bitte Von und Bis wählen.");
    if (form.end < form.start) return setFormError("Das Ende darf nicht vor dem Beginn liegen.");

    const input = { title, kind: form.kind, start_date: form.start, end_date: form.end, child_user_ids: form.childIds };
    setSaving(true);
    setFormError(null);
    try {
      if (editingId) await updateSchoolBreak(editingId, input);
      else await createSchoolBreak(input);
      closeForm();
      await load();
    } catch (err) {
      setFormError(errorText(err, "Konnte nicht gespeichert werden"));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(brk: SchoolBreak) {
    setDeletingId(brk.id);
    setError(null);
    try {
      await deleteSchoolBreak(brk.id);
      setConfirmDeleteId(null);
      if (editingId === brk.id) closeForm();
      await load();
    } catch (err) {
      setConfirmDeleteId(null);
      setError(errorText(err, "Konnte nicht gelöscht werden"));
    } finally {
      setDeletingId(null);
    }
  }

  const { upcoming, past } = sortBreaks(breaks, todayIsoDate());
  const rows = [...upcoming.map((b) => ({ b, past: false })), ...past.map((b) => ({ b, past: true }))];

  return (
    <section className="form-card breaks" aria-labelledby="breaks-heading">
      <div className="breaks-heading">
        <span className="breaks-heading-icon" aria-hidden="true">
          <BeachIcon size={26} />
        </span>
        <h2 id="breaks-heading">Ferien und freie Tage</h2>
        {canEdit && !formOpen && (
          <button type="button" className="breaks-add" onClick={openNew}>
            <span aria-hidden="true">＋</span> Ferien hinzufügen
          </button>
        )}
      </div>

      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}

      {canEdit && formOpen && (
        <form className="breaks-form" noValidate onSubmit={handleSubmit} aria-label={editingId ? "Ferien bearbeiten" : "Ferien hinzufügen"}>
          <label htmlFor="break-title">Name</label>
          <input
            id="break-title"
            type="text"
            value={form.title}
            maxLength={100}
            placeholder="z. B. Herbstferien"
            onChange={(e) => setForm({ ...form, title: e.target.value })}
          />

          <label htmlFor="break-kind">Art</label>
          <select
            id="break-kind"
            value={form.kind}
            onChange={(e) => setForm({ ...form, kind: e.target.value as SchoolBreakKind })}
          >
            <option value="ferien">{BREAK_KIND_LABELS.ferien}</option>
            <option value="schulfrei">{BREAK_KIND_LABELS.schulfrei}</option>
          </select>

          <div className="breaks-dates">
            <div>
              <label htmlFor="break-start">Von</label>
              <input id="break-start" type="date" value={form.start} onChange={(e) => setStart(e.target.value)} />
            </div>
            <div>
              <label htmlFor="break-end">Bis</label>
              <input
                id="break-end"
                type="date"
                value={form.end}
                min={form.start || undefined}
                onChange={(e) => setForm({ ...form, end: e.target.value })}
              />
            </div>
          </div>

          <div className="breaks-children" role="group" aria-label="Kinder">
            <span className="breaks-children-label">Kinder</span>
            <div className="breaks-chips">
              {children.map((child) => {
                const on = form.childIds.includes(child.id);
                const color = colorOf(child);
                return (
                  <button
                    key={child.id}
                    type="button"
                    className={`breaks-chip${on ? " is-on" : ""}`}
                    aria-pressed={on}
                    style={on ? { background: `${color}22`, borderColor: color } : undefined}
                    onClick={() => toggleChild(child.id)}
                  >
                    <span aria-hidden="true">{child.avatar_key || AVATAR_OPTIONS[0]}</span>
                    {child.display_name}
                  </button>
                );
              })}
            </div>
            <p className="card-meta breaks-children-hint">
              {form.childIds.length === 0 ? "Alle Kinder" : "Nur die gewählten Kinder"}
            </p>
          </div>

          {formError && (
            <div className="error-banner" role="alert">
              {formError}
            </div>
          )}

          <div className="template-form-actions">
            <button type="submit" disabled={saving}>
              {saving ? "Wird gespeichert…" : "Speichern"}
            </button>
            <button type="button" className="secondary" onClick={closeForm} disabled={saving}>
              Abbrechen
            </button>
          </div>
        </form>
      )}

      {!loaded ? (
        <p className="card-meta">Wird geladen…</p>
      ) : rows.length === 0 ? (
        <p className="empty-state breaks-empty">Keine Ferien eingetragen.</p>
      ) : (
        <ul className="template-list breaks-list" aria-label="Ferien und freie Tage">
          {rows.map(({ b, past: isPast }) => (
            <li
              key={b.id}
              className={`template-row break-row${isPast ? " break-row--past" : ""}${editingId === b.id ? " editing" : ""}`}
            >
              <span className="break-row-icon" role="img" aria-label={BREAK_KIND_LABELS[b.kind]}>
                <KindIcon kind={b.kind} />
              </span>
              <span className="break-row-main">
                <span className="template-row-title">{b.title}</span>
                <span className="card-meta break-row-range">{formatBreakRange(b.start_date, b.end_date)}</span>
              </span>
              <span className="break-pills" aria-label="Für">
                {b.child_user_ids.length === 0 ? (
                  <span className="break-pill">Alle</span>
                ) : (
                  b.child_user_ids.map((id) => {
                    const child = childById(id);
                    if (!child) return null;
                    const color = colorOf(child);
                    return (
                      <span key={id} className="break-pill" style={{ background: `${color}22`, borderColor: color }}>
                        <span aria-hidden="true">{child.avatar_key || AVATAR_OPTIONS[0]}</span>
                        {child.display_name}
                      </span>
                    );
                  })
                )}
              </span>

              {b.source === "google" ? (
                <span className="break-google">
                  <span className="break-google-mark" role="img" aria-label="Aus Google">
                    G
                  </span>
                  {canEdit && <span className="card-meta">Wird in Google geändert.</span>}
                </span>
              ) : (
                canEdit &&
                (confirmDeleteId === b.id ? (
                  <div className="template-row-confirm">
                    <strong>Löschen?</strong>
                    <button type="button" onClick={() => handleDelete(b)} disabled={deletingId === b.id}>
                      Ja, löschen
                    </button>
                    <button type="button" className="secondary" onClick={() => setConfirmDeleteId(null)}>
                      Abbrechen
                    </button>
                  </div>
                ) : (
                  <div className="template-row-actions">
                    <button
                      type="button"
                      className="icon-btn"
                      aria-label={`Ferien bearbeiten: ${b.title}`}
                      title="Bearbeiten"
                      onClick={() => openEdit(b)}
                    >
                      <span aria-hidden="true">✏️</span>
                    </button>
                    <button
                      type="button"
                      className="secondary icon-btn"
                      aria-label={`Ferien löschen: ${b.title}`}
                      title="Löschen"
                      onClick={() => {
                        setError(null);
                        setConfirmDeleteId(b.id);
                      }}
                    >
                      <span aria-hidden="true">🗑️</span>
                    </button>
                  </div>
                ))
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
