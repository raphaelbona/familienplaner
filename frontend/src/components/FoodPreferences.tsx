import { FormEvent, useEffect, useState } from "react";
import { apiFetch } from "../api/client";

export const MAX_ITEMS_PER_KIND = 50;
export const MAX_ITEM_LENGTH = 80;

type Prefs = { likes: string[]; dislikes: string[]; allergies: string[] };
type Kind = keyof Prefs;

type SectionConfig = { kind: Kind; title: string; placeholder: string; addLabel: string };

const KINDS: SectionConfig[] = [
  { kind: "likes", title: "Ich mag", placeholder: "z.B. Nudeln", addLabel: "Ich mag hinzufügen" },
  { kind: "dislikes", title: "Ich mag nicht", placeholder: "z.B. Pilze", addLabel: "Ich mag nicht hinzufügen" },
  { kind: "allergies", title: "Allergie", placeholder: "z.B. Nüsse", addLabel: "Allergie hinzufügen" }
];

type FoodPreferencesProps = {
  userId: string;
  /** Person's name, shown on the toggle when a parent edits somebody else. */
  name?: string;
  /** Render behind a toggle button and load only when opened. */
  collapsible?: boolean;
};

function PreferenceSection({
  kind,
  title,
  placeholder,
  addLabel,
  items,
  saving,
  onAdd,
  onRemove
}: SectionConfig & {
  items: string[];
  saving: boolean;
  onAdd: (kind: Kind, value: string) => void;
  onRemove: (kind: Kind, value: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const isAllergy = kind === "allergies";
  const full = items.length >= MAX_ITEMS_PER_KIND;

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const value = draft.trim();
    if (!value) return;
    onAdd(kind, value);
    setDraft("");
  }

  return (
    <section className="pref-section" aria-label={title}>
      <h3 className="pref-title">{isAllergy ? `⚠️ ${title}` : title}</h3>
      {items.length === 0 ? (
        <p className="card-meta">Noch nichts eingetragen.</p>
      ) : (
        <ul className="chip-list">
          {items.map((item) => (
            <li key={item} className={`chip${isAllergy ? " chip--warning" : ""}`}>
              {isAllergy && <span className="chip-kind">Allergie:</span>}
              <span>{item}</span>
              <button
                type="button"
                className="chip-remove"
                aria-label={`${item} entfernen`}
                disabled={saving}
                onClick={() => onRemove(kind, item)}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={handleSubmit} className="pref-add">
        <input
          type="text"
          value={draft}
          maxLength={MAX_ITEM_LENGTH}
          placeholder={full ? `Höchstens ${MAX_ITEMS_PER_KIND} Einträge` : placeholder}
          aria-label={`${title}: neuer Eintrag`}
          disabled={full || saving}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button type="submit" disabled={full || saving || !draft.trim()} aria-label={addLabel}>
          Hinzufügen
        </button>
      </form>
    </section>
  );
}

function PreferenceEditor({ userId }: { userId: string }) {
  const [prefs, setPrefs] = useState<Prefs | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setPrefs(null);
    setLoadError(null);
    apiFetch<Prefs>(`/food-preferences/members/${userId}`)
      .then((data) => !cancelled && setPrefs(data))
      .catch((err) => !cancelled && setLoadError(err instanceof Error ? err.message : "Konnte nicht geladen werden"));
    return () => {
      cancelled = true;
    };
  }, [userId]);

  async function save(next: Prefs) {
    setSaveError(null);
    setSaving(true);
    try {
      const saved = await apiFetch<Prefs>(`/food-preferences/members/${userId}`, {
        method: "PUT",
        body: JSON.stringify(next)
      });
      setPrefs(saved);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Konnte nicht gespeichert werden");
    } finally {
      setSaving(false);
    }
  }

  function handleAdd(kind: Kind, value: string) {
    if (!prefs) return;
    const existing = prefs[kind];
    if (existing.some((item) => item.toLowerCase() === value.toLowerCase())) return;
    void save({ ...prefs, [kind]: [...existing, value] });
  }

  function handleRemove(kind: Kind, value: string) {
    if (!prefs) return;
    void save({ ...prefs, [kind]: prefs[kind].filter((item) => item !== value) });
  }

  if (loadError) return <div className="error-banner">{loadError}</div>;
  if (!prefs) return <p className="card-meta">Lädt…</p>;

  return (
    <div>
      {saveError && (
        <div className="error-banner" role="alert">
          {saveError}
        </div>
      )}
      {KINDS.map((section) => (
        <PreferenceSection
          key={section.kind}
          {...section}
          items={prefs[section.kind]}
          saving={saving}
          onAdd={handleAdd}
          onRemove={handleRemove}
        />
      ))}
    </div>
  );
}

export default function FoodPreferences({ userId, name, collapsible = false }: FoodPreferencesProps) {
  const [open, setOpen] = useState(!collapsible);

  if (!collapsible) {
    return (
      <div className="form-card">
        <h2 className="pref-heading">Das mag ich</h2>
        <PreferenceEditor userId={userId} />
      </div>
    );
  }

  return (
    <div className="pref-collapsible">
      <button
        type="button"
        className="secondary"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        style={{ width: "100%" }}
      >
        {`Das mag ich${name ? ` – ${name}` : ""} ${open ? "▴" : "▾"}`}
      </button>
      {open && <PreferenceEditor userId={userId} />}
    </div>
  );
}
