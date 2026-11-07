import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  createDayPhase,
  deleteDayPhase,
  fetchDayPhases,
  seedDefaultDayPhases,
  updateDayPhase,
  type DayPhase,
  type DayPhaseType,
} from "../api/dayPhases";
import { ApiError } from "../api/client";
import { formatHhmm, parseHhmm, phaseRangeLabel, sortPhases } from "../utils/dayPhases";

type Props = {
  /** Called with the current phases after every (re)load, so the page can offer them elsewhere (template form). */
  onChange?: (phases: DayPhase[]) => void;
};

export const DAY_TYPE_LABELS: Record<DayPhaseType, string> = {
  school: "Schultage",
  free: "Freie Tage",
  both: "Beide",
};

export const DAY_TYPE_ICONS: Record<DayPhaseType, string> = {
  school: "🏫",
  free: "🏖️",
  both: "🔁",
};

const DAY_TYPE_ORDER: DayPhaseType[] = ["school", "free", "both"];
const ICON_SUGGESTIONS = ["🌅", "☀️", "🍽️", "🎒", "📚", "🌙", "🛏️"];

const EMPTY_FORM = { name: "", icon: "", start: "", end: "", dayType: "both" as DayPhaseType };

function errorText(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback;
}

/** "Tagesphasen": parents split the day into phases (separate for school days and free days). */
export default function DayPhasesCard({ onChange }: Props) {
  const [phases, setPhases] = useState<DayPhase[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const [formOpen, setFormOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [seeding, setSeeding] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  // Keep the latest callback without making `load` change identity on every parent render.
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  const load = useCallback(() => {
    return fetchDayPhases()
      .then((rows) => {
        const list = Array.isArray(rows) ? rows : [];
        setPhases(list);
        setError(null);
        onChangeRef.current?.(list);
      })
      .catch((err) => setError(errorText(err, "Tagesphasen konnten nicht geladen werden")))
      .finally(() => setLoaded(true));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function openNew() {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setFormError(null);
    setConfirmDeleteId(null);
    setFormOpen(true);
  }

  function openEdit(phase: DayPhase) {
    setEditingId(phase.id);
    setForm({
      name: phase.name,
      icon: phase.icon ?? "",
      start: formatHhmm(phase.start_time),
      end: formatHhmm(phase.end_time),
      dayType: phase.day_type,
    });
    setFormError(null);
    setConfirmDeleteId(null);
    setFormOpen(true);
  }

  function closeForm() {
    setFormOpen(false);
    setEditingId(null);
    setFormError(null);
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const name = form.name.trim();
    if (!name) return setFormError("Bitte einen Namen eingeben.");
    const start = parseHhmm(form.start);
    const end = parseHhmm(form.end);
    if (start === null || end === null) return setFormError("Bitte Von und Bis wählen.");
    if (end <= start) return setFormError("Das Ende muss nach dem Beginn liegen.");

    const input = {
      name,
      icon: form.icon.trim() || null,
      start_time: `${formatHhmm(form.start)}:00`,
      end_time: `${formatHhmm(form.end)}:00`,
      day_type: form.dayType,
    };
    setSaving(true);
    setFormError(null);
    try {
      if (editingId) await updateDayPhase(editingId, input);
      else await createDayPhase(input);
      closeForm();
      await load();
    } catch (err) {
      setFormError(errorText(err, "Konnte nicht gespeichert werden"));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(phase: DayPhase) {
    setDeletingId(phase.id);
    setError(null);
    try {
      await deleteDayPhase(phase.id);
      setConfirmDeleteId(null);
      if (editingId === phase.id) closeForm();
      await load();
    } catch (err) {
      setConfirmDeleteId(null);
      setError(errorText(err, "Konnte nicht gelöscht werden"));
    } finally {
      setDeletingId(null);
    }
  }

  async function handleSeed() {
    setSeeding(true);
    setError(null);
    try {
      await seedDefaultDayPhases();
      await load();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Someone else (or another tab) set them up in the meantime: just show what is there.
        await load();
      } else {
        setError(errorText(err, "Standard-Phasen konnten nicht eingerichtet werden"));
      }
    } finally {
      setSeeding(false);
    }
  }

  const sorted = sortPhases(phases);
  const empty = loaded && phases.length === 0;

  return (
    <section className="form-card day-phases" aria-labelledby="day-phases-heading">
      <div className="breaks-heading">
        <span className="breaks-heading-icon" aria-hidden="true">
          ⏰
        </span>
        <h3 id="day-phases-heading" style={{ margin: 0, flex: "1 1 auto", fontSize: 16 }}>
          Tagesphasen
        </h3>
        {!formOpen && (
          <button type="button" className="breaks-add" onClick={openNew}>
            <span aria-hidden="true">＋</span> Phase hinzufügen
          </button>
        )}
      </div>

      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}

      {formOpen && (
        <form
          className="breaks-form"
          noValidate
          onSubmit={handleSubmit}
          aria-label={editingId ? "Phase bearbeiten" : "Phase hinzufügen"}
        >
          <label htmlFor="phase-name">Name</label>
          <input
            id="phase-name"
            type="text"
            value={form.name}
            maxLength={50}
            placeholder="z. B. Morgen"
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />

          <label htmlFor="phase-icon">Icon</label>
          <input
            id="phase-icon"
            type="text"
            value={form.icon}
            maxLength={8}
            placeholder="🌅"
            onChange={(e) => setForm({ ...form, icon: e.target.value })}
          />
          <div className="breaks-chips phase-icon-suggestions" role="group" aria-label="Icon-Vorschläge">
            {ICON_SUGGESTIONS.map((emoji) => (
              <button
                key={emoji}
                type="button"
                className={`breaks-chip phase-icon-chip${form.icon === emoji ? " is-on" : ""}`}
                aria-pressed={form.icon === emoji}
                aria-label={`Icon ${emoji}`}
                onClick={() => setForm({ ...form, icon: emoji })}
              >
                <span aria-hidden="true">{emoji}</span>
              </button>
            ))}
          </div>

          <div className="breaks-dates">
            <div>
              <label htmlFor="phase-start">Von</label>
              <input
                id="phase-start"
                type="time"
                value={form.start}
                onChange={(e) => setForm({ ...form, start: e.target.value })}
              />
            </div>
            <div>
              <label htmlFor="phase-end">Bis</label>
              <input
                id="phase-end"
                type="time"
                value={form.end}
                onChange={(e) => setForm({ ...form, end: e.target.value })}
              />
            </div>
          </div>

          <div className="breaks-children" role="group" aria-label="Gilt an">
            <span className="breaks-children-label">Gilt an</span>
            <div className="breaks-chips">
              {DAY_TYPE_ORDER.map((type) => {
                const on = form.dayType === type;
                return (
                  <button
                    key={type}
                    type="button"
                    className={`breaks-chip phase-chip-select${on ? " is-on" : ""}`}
                    aria-pressed={on}
                    onClick={() => setForm({ ...form, dayType: type })}
                  >
                    <span aria-hidden="true">{DAY_TYPE_ICONS[type]}</span>
                    {DAY_TYPE_LABELS[type]}
                  </button>
                );
              })}
            </div>
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
      ) : empty ? (
        <div className="day-phases-empty">
          <p className="empty-state breaks-empty">Noch keine Tagesphasen.</p>
          {!formOpen && (
            <button type="button" onClick={handleSeed} disabled={seeding}>
              <span aria-hidden="true">✨</span> {seeding ? "Wird eingerichtet…" : "Standard-Phasen einrichten"}
            </button>
          )}
        </div>
      ) : (
        DAY_TYPE_ORDER.map((type) => {
          const rows = sorted.filter((phase) => phase.day_type === type);
          if (rows.length === 0) return null;
          const label = `${DAY_TYPE_ICONS[type]} ${DAY_TYPE_LABELS[type]}`;
          return (
            <div key={type} className="phase-type-group">
              <h4 className="phase-type-heading">{label}</h4>
              <ul className="template-list" aria-label={label}>
                {rows.map((phase) => (
                  <li key={phase.id} className={`template-row${editingId === phase.id ? " editing" : ""}`}>
                    <span className="template-row-icon" aria-hidden="true">
                      {phase.icon || "🕒"}
                    </span>
                    <span className="break-row-main">
                      <span className="template-row-title">{phase.name}</span>
                      <span className="card-meta break-row-range">{phaseRangeLabel(phase)}</span>
                    </span>
                    {confirmDeleteId === phase.id ? (
                      <div className="template-row-confirm">
                        <strong>Löschen?</strong>
                        <button type="button" onClick={() => handleDelete(phase)} disabled={deletingId === phase.id}>
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
                          aria-label={`Phase bearbeiten: ${phase.name}`}
                          title="Bearbeiten"
                          onClick={() => openEdit(phase)}
                        >
                          <span aria-hidden="true">✏️</span>
                        </button>
                        <button
                          type="button"
                          className="secondary icon-btn"
                          aria-label={`Phase löschen: ${phase.name}`}
                          title="Löschen"
                          onClick={() => {
                            setError(null);
                            setConfirmDeleteId(phase.id);
                          }}
                        >
                          <span aria-hidden="true">🗑️</span>
                        </button>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          );
        })
      )}
    </section>
  );
}
