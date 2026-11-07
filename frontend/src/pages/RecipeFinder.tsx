import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { apiFetch, decodeRoleFromToken, getToken } from "../api/client";
import { FamilyMember, fetchFamilyMembers } from "../api/members";
import Dialog from "../components/Dialog";
import { MEAL_TYPES, MealType } from "../constants/meals";
import { todayIsoDate } from "../utils/date";
import { formatRating, isHttpsUrl, isParentRole } from "../utils/rating";

/** One result of POST /recipes/discover. Proven items carry `id` (family recipe), new ones `candidate_id`. */
type DiscoverItem = {
  id?: string;
  candidate_id?: string;
  title: string;
  image_url: string | null;
  prep_minutes: number | null;
  source_name: string | null;
  source_url: string | null;
  ingredients: string[];
  age_ok: boolean;
  age_hint: string;
  balanced: boolean;
  balance_label: string;
  language?: "de" | "en";
  avg_stars: number | null;
  rating_count: number;
};

type DiscoverResponse = { proven: DiscoverItem[]; discover: DiscoverItem[]; source_warnings?: string[] };

const MAX_EXTRA_INGREDIENTS = 10;
const MAX_EXTRA_LENGTH = 60;

function RecipeImage({ url, className }: { url: string | null; className: string }) {
  const [failed, setFailed] = useState(false);
  if (!isHttpsUrl(url) || failed) {
    return (
      <div className={`${className} recipe-image-placeholder`} aria-hidden="true">
        🍽️
      </div>
    );
  }
  return (
    <img
      className={className}
      src={url}
      alt=""
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
    />
  );
}

function ItemBadges({ item }: { item: DiscoverItem }) {
  return (
    <div className="recipe-badges">
      {item.rating_count > 0 && item.avg_stars !== null && (
        <span className="status-pill accent">{formatRating(item.avg_stars, item.rating_count)}</span>
      )}
      {item.language === "en" && (
        <span className="status-pill" title="Dieses Rezept ist auf Englisch">
          🇬🇧 Englisch
        </span>
      )}
      {item.prep_minutes ? <span className="status-pill">⏱ {item.prep_minutes} Min.</span> : null}
      {item.age_hint && <span className="status-pill sky">👶 {item.age_hint}</span>}
      {item.balance_label && (
        <span className={`status-pill${item.balanced ? " leaf" : ""}`}>
          {item.balanced ? "🥗" : "⚖️"} {item.balance_label}
        </span>
      )}
    </div>
  );
}

function RecipeCard({ item, onOpen }: { item: DiscoverItem; onOpen: (item: DiscoverItem) => void }) {
  return (
    <button type="button" className="recipe-card" onClick={() => onOpen(item)}>
      <RecipeImage url={item.image_url} className="recipe-card-image" />
      <span className="recipe-card-body">
        <span className="recipe-card-title">{item.title}</span>
        <ItemBadges item={item} />
        {item.source_name && <span className="card-meta">{item.source_name}</span>}
      </span>
    </button>
  );
}

function SkeletonCards() {
  return (
    <div className="recipe-grid" aria-busy="true" aria-label="Rezepte werden gesucht">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="recipe-card recipe-skeleton" aria-hidden="true" />
      ))}
    </div>
  );
}

function RecipeDetail({ item, defaultMealType, onClose }: { item: DiscoverItem; defaultMealType: MealType; onClose: () => void }) {
  const [date, setDate] = useState(todayIsoDate());
  const [mealType, setMealType] = useState<MealType>(defaultMealType);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [planned, setPlanned] = useState(false);

  async function handlePlan(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSaving(true);
    try {
      if (item.candidate_id) {
        await apiFetch("/recipes/discover/adopt", {
          method: "POST",
          body: JSON.stringify({ candidate_id: item.candidate_id, plan_date: date, meal_type: mealType })
        });
      } else {
        await apiFetch("/meal-plan/entries", {
          method: "POST",
          body: JSON.stringify({ date, meal_type: mealType, recipe_id: item.id })
        });
      }
      setPlanned(true);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Konnte nicht eingeplant werden";
      if (!/not found|expired/i.test(message)) {
        setError(message);
      } else if (item.candidate_id) {
        setError("Dieser Vorschlag ist abgelaufen. Bitte suche erneut.");
      } else {
        setError("Rezept nicht gefunden.");
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog title={item.title} onClose={onClose}>
      <RecipeImage url={item.image_url} className="recipe-detail-image" />
      <ItemBadges item={item} />

      <h3 className="pref-title" style={{ marginTop: 16 }}>
        Zutaten
      </h3>
      {item.ingredients.length === 0 ? (
        <p className="card-meta">Keine Zutaten angegeben.</p>
      ) : (
        <ul className="ingredient-list">
          {item.ingredients.map((ingredient, index) => (
            <li key={`${ingredient}-${index}`}>{ingredient}</li>
          ))}
        </ul>
      )}

      {isHttpsUrl(item.source_url) && (
        <p style={{ marginTop: 12 }}>
          <a href={item.source_url} target="_blank" rel="noopener noreferrer">
            Rezept bei {item.source_name || "der Quelle"} ansehen ↗
          </a>
        </p>
      )}

      {planned ? (
        <div className="dialog-notice" role="status">
          Eingeplant! <Link to="/essensplan">Zum Essensplan →</Link>
        </div>
      ) : (
        <form onSubmit={handlePlan} className="plan-form">
          <h3 className="pref-title">Einplanen</h3>
          <label htmlFor="plan-date">Datum</label>
          <input id="plan-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
          <label htmlFor="plan-meal">Mahlzeit</label>
          <select id="plan-meal" value={mealType} onChange={(e) => setMealType(e.target.value as MealType)}>
            {MEAL_TYPES.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
          {error && (
            <div className="error-banner" role="alert">
              {error}
            </div>
          )}
          <button type="submit" disabled={saving || !date}>
            {saving ? "Wird eingeplant…" : "Einplanen"}
          </button>
        </form>
      )}
    </Dialog>
  );
}

function ResultSection({ title, items, onOpen }: { title: string; items: DiscoverItem[]; onOpen: (item: DiscoverItem) => void }) {
  if (items.length === 0) return null;
  return (
    <section aria-label={title}>
      <h2 className="finder-section-title">{title}</h2>
      <div className="recipe-grid">
        {items.map((item) => (
          <RecipeCard key={item.candidate_id ?? item.id ?? item.title} item={item} onOpen={onOpen} />
        ))}
      </div>
    </section>
  );
}

export default function RecipeFinder() {
  const navigate = useNavigate();
  const role = decodeRoleFromToken(getToken() ?? "");
  const isParent = isParentRole(role);

  const [members, setMembers] = useState<FamilyMember[]>([]);
  const [membersLoading, setMembersLoading] = useState(true);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [mealType, setMealType] = useState<MealType>("dinner");
  const [extras, setExtras] = useState<string[]>([]);
  const [extraDraft, setExtraDraft] = useState("");

  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<DiscoverResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedItem, setSelectedItem] = useState<DiscoverItem | null>(null);

  useEffect(() => {
    if (!isParent) {
      navigate("/");
      return;
    }
    fetchFamilyMembers()
      .then(setMembers)
      .catch((err) => setError(err instanceof Error ? err.message : "Familie konnte nicht geladen werden"))
      .finally(() => setMembersLoading(false));
  }, [isParent, navigate]);

  if (!isParent) return null;

  const selectedMembers = members.filter((m) => selectedIds.includes(m.id));
  const likedIngredients = [...new Set(selectedMembers.flatMap((m) => m.likes))];
  const allergyNotes = selectedMembers.flatMap((m) => m.allergies.map((a) => `Allergie ${a} (${m.display_name})`));

  function toggleMember(id: string) {
    setSelectedIds((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]));
  }

  function addExtra(event: FormEvent) {
    event.preventDefault();
    const value = extraDraft.trim();
    if (!value) return;
    if (!extras.some((e) => e.toLowerCase() === value.toLowerCase())) setExtras([...extras, value]);
    setExtraDraft("");
  }

  async function handleSearch() {
    setError(null);
    setResults(null);
    setSearching(true);
    try {
      const data = await apiFetch<DiscoverResponse>("/recipes/discover", {
        method: "POST",
        body: JSON.stringify({ eater_user_ids: selectedIds, meal_type: mealType, extra_ingredients: extras })
      });
      setResults(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Die Suche ist fehlgeschlagen. Bitte versuche es erneut.");
    } finally {
      setSearching(false);
    }
  }

  const nothingFound = results !== null && results.proven.length === 0 && results.discover.length === 0;

  return (
    <>
      <h1>Rezepte finden</h1>
      <p className="subtitle">Wer isst mit? Wir suchen passende Rezepte und beachten Vorlieben und Allergien.</p>

      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}

      <div className="form-card">
        <h2 className="pref-heading">Wer isst mit?</h2>
        {membersLoading ? (
          <p className="card-meta">Lädt…</p>
        ) : (
          <ul className="chip-list" aria-label="Familienmitglieder">
            {members.map((m) => (
              <li key={m.id}>
                <button
                  type="button"
                  className={`member-chip${selectedIds.includes(m.id) ? " is-selected" : ""}`}
                  aria-pressed={selectedIds.includes(m.id)}
                  onClick={() => toggleMember(m.id)}
                >
                  <span aria-hidden="true">{m.avatar_key ?? "🧑"}</span> {m.display_name}
                </button>
              </li>
            ))}
          </ul>
        )}

        <label htmlFor="finder-meal" style={{ marginTop: 14 }}>
          Mahlzeit
        </label>
        <select id="finder-meal" value={mealType} onChange={(e) => setMealType(e.target.value as MealType)}>
          {MEAL_TYPES.map((m) => (
            <option key={m.value} value={m.value}>
              {m.label}
            </option>
          ))}
        </select>

        {allergyNotes.length > 0 && (
          <p className="allergy-note" role="note">
            ⚠️ Beachtet: {allergyNotes.join(", ")}
          </p>
        )}

        {likedIngredients.length > 0 && (
          <>
            <h3 className="pref-title">Das mögen die Gewählten</h3>
            <ul className="chip-list" aria-label="Beliebte Zutaten">
              {likedIngredients.map((item) => (
                <li key={item} className="chip">
                  {item}
                </li>
              ))}
            </ul>
          </>
        )}

        <h3 className="pref-title">Zusätzliche Wunschzutaten (optional)</h3>
        {extras.length > 0 && (
          <ul className="chip-list">
            {extras.map((item) => (
              <li key={item} className="chip">
                <span>{item}</span>
                <button
                  type="button"
                  className="chip-remove"
                  aria-label={`${item} entfernen`}
                  onClick={() => setExtras(extras.filter((e) => e !== item))}
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
        )}
        <form onSubmit={addExtra} className="pref-add">
          <input
            type="text"
            value={extraDraft}
            maxLength={MAX_EXTRA_LENGTH}
            aria-label="Wunschzutat"
            placeholder="z.B. Zucchini"
            disabled={extras.length >= MAX_EXTRA_INGREDIENTS}
            onChange={(e) => setExtraDraft(e.target.value)}
          />
          <button type="submit" disabled={!extraDraft.trim() || extras.length >= MAX_EXTRA_INGREDIENTS} aria-label="Wunschzutat hinzufügen">
            Hinzufügen
          </button>
        </form>

        <button
          type="button"
          onClick={handleSearch}
          disabled={selectedIds.length === 0 || searching}
          style={{ width: "100%", marginTop: 8 }}
        >
          {searching ? "Suche läuft…" : "Rezepte suchen"}
        </button>
        {selectedIds.length === 0 && !membersLoading && <p className="card-meta">Wähle mindestens eine Person aus.</p>}
      </div>

      {searching && <SkeletonCards />}

      {results?.source_warnings && results.source_warnings.length > 0 && (
        <p className="error-banner" role="status">
          {results.source_warnings.join(" ")}
        </p>
      )}

      {nothingFound && (
        <p className="empty-state">
          {results?.source_warnings?.length
            ? "Es konnten keine Rezepte geladen werden. Bitte versuche es später noch einmal."
            : "Keine passenden Rezepte gefunden – bewährte Rezepte erscheinen nach der ersten Bewertung."}
        </p>
      )}

      {results && (
        <>
          <ResultSection title="Schon getestet" items={results.proven} onOpen={setSelectedItem} />
          <ResultSection title="Neu entdecken" items={results.discover} onOpen={setSelectedItem} />
          {(results.proven.length > 0 || results.discover.length > 0) && (
            <p className="card-meta" role="note">
              Hinweis: Der Allergie-Filter ist eine Hilfe. Bitte prüfe die Zutatenliste im Rezept immer selbst.
            </p>
          )}
        </>
      )}

      {selectedItem && <RecipeDetail item={selectedItem} defaultMealType={mealType} onClose={() => setSelectedItem(null)} />}
    </>
  );
}
