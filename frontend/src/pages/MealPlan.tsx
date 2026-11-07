import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch, decodeRoleFromToken, getToken } from "../api/client";
import RateMealDialog from "../components/RateMealDialog";
import { MEAL_LABELS } from "../constants/meals";
import { localIsoDateOffset, todayIsoDate } from "../utils/date";
import { formatRating, isParentRole } from "../utils/rating";

type MealPlanEntry = {
  id: string;
  date: string;
  meal_type: string;
  recipe_id: string;
  recipe_name: string;
};

/** Only meals of the last days show a family average; older history is not queried. */
const SUMMARY_WINDOW_DAYS = 14;

type RatingSummary = { average: number | null; rating_count: number };

const MEAL_ICONS: Record<string, string> = {
  breakfast: "🍳",
  lunch: "🥪",
  dinner: "🍲"
};

export default function MealPlan() {
  const role = decodeRoleFromToken(getToken() ?? "");
  const isDashboard = role === "dashboard";
  const isParent = isParentRole(role);

  const [entries, setEntries] = useState<MealPlanEntry[]>([]);
  const [summaries, setSummaries] = useState<Record<string, RatingSummary>>({});
  const [ratingEntry, setRatingEntry] = useState<MealPlanEntry | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [fromDate, setFromDate] = useState(todayIsoDate());
  const [toDate, setToDate] = useState(localIsoDateOffset(6));
  const [generating, setGenerating] = useState(false);
  const [generateNotice, setGenerateNotice] = useState<string | null>(null);

  // A missing summary is not worth an error banner: the entry simply shows no average.
  function loadSummary(recipeId: string) {
    return apiFetch<RatingSummary>(`/recipes/${recipeId}/ratings-summary`)
      .then((summary) => setSummaries((current) => ({ ...current, [recipeId]: summary })))
      .catch(() => undefined);
  }

  function loadSummaries(list: MealPlanEntry[]) {
    const today = todayIsoDate();
    const earliest = localIsoDateOffset(-SUMMARY_WINDOW_DAYS);
    const recipeIds = new Set(list.filter((e) => e.date >= earliest && e.date <= today).map((e) => e.recipe_id));
    recipeIds.forEach((id) => void loadSummary(id));
  }

  function loadEntries() {
    apiFetch<MealPlanEntry[]>("/meal-plan/entries")
      .then((list) => {
        setEntries(list);
        loadSummaries(list);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Essensplan konnte nicht geladen werden"));
  }

  useEffect(loadEntries, []);

  async function handleGenerateShoppingList(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setGenerateNotice(null);
    setGenerating(true);
    try {
      await apiFetch(
        `/shopping-lists/from-meal-plan?from_date=${fromDate}&to_date=${toDate}`,
        { method: "POST" }
      );
      setGenerateNotice("Einkaufsliste wurde erstellt.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Einkaufsliste konnte nicht erstellt werden");
    } finally {
      setGenerating(false);
    }
  }

  return (
    <>
      <h1>Essensplan</h1>
      <p className="subtitle">
        {isDashboard ? (
          "Was diese Woche geplant ist."
        ) : (
          <>
            Rezepte für die Woche planen. Passende Rezepte findest du unter <Link to="/rezepte">Rezepte</Link>.
          </>
        )}
      </p>

      {isParent && (
        <Link to="/rezepte/finden" className="btn btn-link" style={{ marginBottom: 20 }}>
          🔍 Rezepte finden
        </Link>
      )}

      {error && <div className="error-banner">{error}</div>}

      {!isDashboard && (
        <div className="form-card" style={{ maxWidth: "none", marginBottom: 22 }}>
          <h2 style={{ marginTop: 0, marginBottom: 16, fontSize: "16px" }}>Einkaufsliste generieren</h2>
          <p className="card-meta" style={{ marginBottom: 12 }}>
            Sammelt alle Zutaten der geplanten Rezepte im Zeitraum, die noch nicht in der Vorratskammer sind.
          </p>
          {generateNotice && (
            <div className="card" style={{ marginBottom: 12 }}>
              {generateNotice}{" "}
              <Link to="/einkaufsliste" style={{ marginLeft: 6 }}>
                Zur Einkaufsliste →
              </Link>
            </div>
          )}
          <form onSubmit={handleGenerateShoppingList} style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} required />
            <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} required />
            <button type="submit" disabled={generating}>
              {generating ? "Wird erstellt…" : "Einkaufsliste generieren"}
            </button>
          </form>
        </div>
      )}

      <h2 style={{ marginBottom: 14, fontSize: "16px" }}>Geplante Mahlzeiten</h2>
      {entries.length === 0 ? (
        <p className="empty-state">Noch kein Essensplan für diese Woche.</p>
      ) : (
        entries.map((entry) => {
          const summary = summaries[entry.recipe_id];
          const canRate = !isDashboard && entry.date <= todayIsoDate();
          return (
          <div key={entry.id} className="card">
            <div className="icon-chip leaf" aria-hidden="true">
              {MEAL_ICONS[entry.meal_type] ?? "🍽️"}
            </div>
            <div className="card-body">
              <div className="card-title">{entry.recipe_name}</div>
              <div className="card-meta">
                {MEAL_LABELS[entry.meal_type] ?? entry.meal_type} ·{" "}
                {new Date(entry.date).toLocaleDateString("de-CH")}
              </div>
              {summary && summary.average !== null && summary.rating_count > 0 && (
                <div className="card-meta">Familie: {formatRating(summary.average, summary.rating_count)}</div>
              )}
              {canRate && (
                <button
                  type="button"
                  className="secondary"
                  style={{ marginTop: 8 }}
                  aria-label={`${entry.recipe_name} bewerten`}
                  onClick={() => setRatingEntry(entry)}
                >
                  ⭐ Bewerten
                </button>
              )}
            </div>
          </div>
          );
        })
      )}

      {ratingEntry && (
        <RateMealDialog
          entry={ratingEntry}
          isParent={isParent}
          onClose={() => setRatingEntry(null)}
          onRated={() => void loadSummary(ratingEntry.recipe_id)}
        />
      )}
    </>
  );
}
