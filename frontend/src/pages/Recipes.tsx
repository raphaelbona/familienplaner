import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch, decodeRoleFromToken, getToken } from "../api/client";
import { MEAL_TYPES } from "../constants/meals";
import { isParentRole } from "../utils/rating";

type Ingredient = {
  id: string;
  name: string;
  category: string | null;
  default_unit: string;
};

type Recipe = {
  id: string;
  name: string;
  instructions: string;
  servings: number;
  prep_minutes: number;
};

type RecipeSuggestion = {
  recipe: Recipe;
  matched_ingredients: number;
  missing_ingredients: number;
};

type RecipeIngredientRow = {
  ingredientId: string;
  quantity: string;
  unit: string;
};

function AddToMealPlanForm({ recipeId, onAdded }: { recipeId: string; onAdded: () => void }) {
  const [date, setDate] = useState("");
  const [mealType, setMealType] = useState("dinner");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSaving(true);
    try {
      await apiFetch("/meal-plan/entries", {
        method: "POST",
        body: JSON.stringify({ date, meal_type: mealType, recipe_id: recipeId })
      });
      setDate("");
      onAdded();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Konnte nicht hinzugefügt werden");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
      <input
        type="date"
        value={date}
        onChange={(e) => setDate(e.target.value)}
        required
        style={{ marginBottom: 0 }}
      />
      <select value={mealType} onChange={(e) => setMealType(e.target.value)} style={{ marginBottom: 0 }}>
        {MEAL_TYPES.map((m) => (
          <option key={m.value} value={m.value}>
            {m.label}
          </option>
        ))}
      </select>
      <button type="submit" disabled={saving} style={{ fontSize: "13px", padding: "6px 12px" }}>
        {saving ? "…" : "Zum Essensplan hinzufügen"}
      </button>
      {error && <span style={{ color: "var(--error, #c0392b)", fontSize: "12.5px" }}>{error}</span>}
    </form>
  );
}

export default function Recipes() {
  const isParent = isParentRole(decodeRoleFromToken(getToken() ?? ""));
  const [ingredients, setIngredients] = useState<Ingredient[]>([]);
  const [recipes, setRecipes] = useState<Recipe[]>([]);
  const [error, setError] = useState<string | null>(null);

  // New ingredient form
  const [newIngredientName, setNewIngredientName] = useState("");
  const [creatingIngredient, setCreatingIngredient] = useState(false);

  // New recipe form
  const [recipeName, setRecipeName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [servings, setServings] = useState("4");
  const [prepMinutes, setPrepMinutes] = useState("30");
  const [ingredientRows, setIngredientRows] = useState<RecipeIngredientRow[]>([
    { ingredientId: "", quantity: "1", unit: "stk" }
  ]);
  const [creatingRecipe, setCreatingRecipe] = useState(false);

  // Wunschzutaten / suggestions
  const [selectedIngredientIds, setSelectedIngredientIds] = useState<string[]>([]);
  const [suggestions, setSuggestions] = useState<RecipeSuggestion[] | null>(null);
  const [suggesting, setSuggesting] = useState(false);

  function loadData() {
    Promise.all([apiFetch<Ingredient[]>("/ingredients"), apiFetch<Recipe[]>("/recipes")])
      .then(([ingredientsData, recipesData]) => {
        setIngredients(ingredientsData);
        setRecipes(recipesData);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Konnte nicht geladen werden"));
  }

  useEffect(loadData, []);

  async function handleCreateIngredient(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setCreatingIngredient(true);
    try {
      await apiFetch("/ingredients", {
        method: "POST",
        body: JSON.stringify({ name: newIngredientName })
      });
      setNewIngredientName("");
      loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Zutat konnte nicht erstellt werden");
    } finally {
      setCreatingIngredient(false);
    }
  }

  function updateIngredientRow(index: number, changes: Partial<RecipeIngredientRow>) {
    setIngredientRows((rows) => rows.map((row, i) => (i === index ? { ...row, ...changes } : row)));
  }

  function addIngredientRow() {
    setIngredientRows((rows) => [...rows, { ingredientId: "", quantity: "1", unit: "stk" }]);
  }

  function removeIngredientRow(index: number) {
    setIngredientRows((rows) => rows.filter((_, i) => i !== index));
  }

  async function handleCreateRecipe(event: FormEvent) {
    event.preventDefault();
    setError(null);

    const validRows = ingredientRows.filter((row) => row.ingredientId);
    if (validRows.length === 0) {
      setError("Mindestens eine Zutat auswählen");
      return;
    }

    setCreatingRecipe(true);
    try {
      await apiFetch("/recipes", {
        method: "POST",
        body: JSON.stringify({
          name: recipeName,
          instructions,
          servings: parseInt(servings, 10) || 4,
          prep_minutes: parseInt(prepMinutes, 10) || 30,
          ingredients: validRows.map((row) => ({
            ingredient_id: row.ingredientId,
            quantity: parseFloat(row.quantity) || 1,
            unit: row.unit
          }))
        })
      });
      setRecipeName("");
      setInstructions("");
      setServings("4");
      setPrepMinutes("30");
      setIngredientRows([{ ingredientId: "", quantity: "1", unit: "stk" }]);
      loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Rezept konnte nicht erstellt werden");
    } finally {
      setCreatingRecipe(false);
    }
  }

  function toggleWunschzutat(ingredientId: string) {
    setSelectedIngredientIds((ids) =>
      ids.includes(ingredientId) ? ids.filter((id) => id !== ingredientId) : [...ids, ingredientId]
    );
  }

  async function handleSuggest() {
    setError(null);
    setSuggesting(true);
    try {
      const result = await apiFetch<RecipeSuggestion[]>("/recipes/suggest", {
        method: "POST",
        body: JSON.stringify({ available_ingredient_ids: selectedIngredientIds })
      });
      setSuggestions(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Vorschläge konnten nicht geladen werden");
    } finally {
      setSuggesting(false);
    }
  }

  return (
    <>
      <h1>Rezepte</h1>
      <p className="subtitle">Wähle deine Wunschzutaten und finde passende Rezepte.</p>

      {isParent && (
        <Link to="/rezepte/finden" className="btn btn-link" style={{ marginBottom: 20 }}>
          🔍 Neue Rezepte finden
        </Link>
      )}

      {error && <div className="error-banner">{error}</div>}

      {/* Wunschzutaten / suggestions */}
      <div className="form-card" style={{ maxWidth: "none", marginBottom: 22 }}>
        <h2 style={{ marginTop: 0, marginBottom: 16, fontSize: "16px" }}>Rezeptvorschläge aus Wunschzutaten</h2>
        {ingredients.length === 0 ? (
          <p className="card-meta">Zuerst unten ein paar Zutaten anlegen.</p>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: "8px", marginBottom: 16 }}>
            {ingredients.map((ingredient) => (
              <label
                key={ingredient.id}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "6px",
                  background: selectedIngredientIds.includes(ingredient.id) ? "var(--accent)" : "var(--surface-2)",
                  color: selectedIngredientIds.includes(ingredient.id) ? "white" : "inherit",
                  padding: "6px 12px",
                  borderRadius: "999px",
                  fontSize: "13.5px",
                  cursor: "pointer"
                }}
              >
                <input
                  type="checkbox"
                  checked={selectedIngredientIds.includes(ingredient.id)}
                  onChange={() => toggleWunschzutat(ingredient.id)}
                  style={{ width: "auto", marginBottom: 0 }}
                />
                {ingredient.name}
              </label>
            ))}
          </div>
        )}
        <button type="button" disabled={suggesting || selectedIngredientIds.length === 0} onClick={handleSuggest}>
          {suggesting ? "Wird gesucht…" : "Vorschläge anzeigen"}
        </button>
      </div>

      {suggestions && (
        <>
          <h2 style={{ marginBottom: 14, fontSize: "16px" }}>Vorschläge</h2>
          {suggestions.length === 0 ? (
            <p className="empty-state">Keine passenden Rezepte gefunden.</p>
          ) : (
            suggestions.map((suggestion) => (
              <div key={suggestion.recipe.id} className="card">
                <div className="icon-chip leaf" aria-hidden="true">
                  🍽️
                </div>
                <div className="card-body">
                  <div className="card-title">{suggestion.recipe.name}</div>
                  <div className="card-meta">
                    {suggestion.matched_ingredients} passende Zutat(en)
                    {suggestion.missing_ingredients > 0 && `, ${suggestion.missing_ingredients} fehlen`} ·{" "}
                    {suggestion.recipe.prep_minutes} Min. · {suggestion.recipe.servings} Portionen
                  </div>
                  <AddToMealPlanForm recipeId={suggestion.recipe.id} onAdded={() => {}} />
                </div>
              </div>
            ))
          )}
        </>
      )}

      {/* New recipe */}
      <div className="form-card" style={{ maxWidth: "none", marginTop: 28, marginBottom: 22 }}>
        <h2 style={{ marginTop: 0, marginBottom: 16, fontSize: "16px" }}>Neues Rezept</h2>
        <form onSubmit={handleCreateRecipe}>
          <label htmlFor="recipe-name">Name</label>
          <input id="recipe-name" value={recipeName} onChange={(e) => setRecipeName(e.target.value)} required />

          <label htmlFor="recipe-instructions">Zubereitung</label>
          <textarea
            id="recipe-instructions"
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            rows={3}
            required
            style={{ width: "100%", fontFamily: "inherit", fontSize: "inherit" }}
          />

          <div style={{ display: "flex", gap: 12 }}>
            <div style={{ flex: 1 }}>
              <label htmlFor="recipe-servings">Portionen</label>
              <input
                id="recipe-servings"
                type="number"
                min="1"
                value={servings}
                onChange={(e) => setServings(e.target.value)}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label htmlFor="recipe-prep">Zubereitungszeit (Min.)</label>
              <input
                id="recipe-prep"
                type="number"
                min="1"
                value={prepMinutes}
                onChange={(e) => setPrepMinutes(e.target.value)}
              />
            </div>
          </div>

          <label>Zutaten</label>
          {ingredientRows.map((row, index) => (
            <div key={index} style={{ display: "flex", gap: 8, marginBottom: 8, alignItems: "center" }}>
              <select
                value={row.ingredientId}
                onChange={(e) => updateIngredientRow(index, { ingredientId: e.target.value })}
                style={{ flex: 2, marginBottom: 0 }}
              >
                <option value="">– Zutat wählen –</option>
                {ingredients.map((ingredient) => (
                  <option key={ingredient.id} value={ingredient.id}>
                    {ingredient.name}
                  </option>
                ))}
              </select>
              <input
                type="number"
                min="0"
                step="0.1"
                value={row.quantity}
                onChange={(e) => updateIngredientRow(index, { quantity: e.target.value })}
                style={{ flex: 1, marginBottom: 0 }}
                placeholder="Menge"
              />
              <input
                value={row.unit}
                onChange={(e) => updateIngredientRow(index, { unit: e.target.value })}
                style={{ flex: 1, marginBottom: 0 }}
                placeholder="Einheit"
              />
              {ingredientRows.length > 1 && (
                <button type="button" onClick={() => removeIngredientRow(index)} style={{ padding: "6px 10px" }}>
                  ✕
                </button>
              )}
            </div>
          ))}
          <button type="button" onClick={addIngredientRow} style={{ marginBottom: 16, fontSize: "13px" }}>
            + Zutat hinzufügen
          </button>

          <button type="submit" disabled={creatingRecipe}>
            {creatingRecipe ? "Wird erstellt…" : "Rezept erstellen"}
          </button>
        </form>
      </div>

      {/* Ingredient catalog */}
      <div className="form-card" style={{ maxWidth: "none", marginBottom: 22 }}>
        <h2 style={{ marginTop: 0, marginBottom: 16, fontSize: "16px" }}>Neue Zutat</h2>
        <form onSubmit={handleCreateIngredient} style={{ display: "flex", gap: 8 }}>
          <input
            value={newIngredientName}
            onChange={(e) => setNewIngredientName(e.target.value)}
            placeholder="z.B. Tomate"
            required
            style={{ flex: 1, marginBottom: 0 }}
          />
          <button type="submit" disabled={creatingIngredient}>
            {creatingIngredient ? "…" : "Hinzufügen"}
          </button>
        </form>
      </div>

      <h2 style={{ marginBottom: 14, fontSize: "16px" }}>Alle Rezepte</h2>
      {recipes.length === 0 ? (
        <p className="empty-state">Noch keine Rezepte vorhanden.</p>
      ) : (
        recipes.map((recipe) => (
          <div key={recipe.id} className="card">
            <div className="icon-chip leaf" aria-hidden="true">
              🍽️
            </div>
            <div className="card-body">
              <div className="card-title">{recipe.name}</div>
              <div className="card-meta">
                {recipe.prep_minutes} Min. · {recipe.servings} Portionen
              </div>
              <AddToMealPlanForm recipeId={recipe.id} onAdded={() => {}} />
            </div>
          </div>
        ))
      )}
    </>
  );
}
