import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";

type ShoppingListItem = {
  id: string;
  ingredient_id: string;
  ingredient_name: string;
  quantity: number;
  unit: string;
  checked: boolean;
};

type ShoppingList = {
  id: string;
  status: "open" | "done";
  source: "manual" | "meal_plan";
  items: ShoppingListItem[];
};

export default function ShoppingListPage() {
  const [lists, setLists] = useState<ShoppingList[]>([]);
  const [error, setError] = useState<string | null>(null);

  function loadLists() {
    apiFetch<ShoppingList[]>("/shopping-lists")
      .then(setLists)
      .catch((err) => setError(err instanceof Error ? err.message : "Einkaufsliste konnte nicht geladen werden"));
  }

  useEffect(loadLists, []);

  async function toggleItem(listId: string, itemId: string, currentChecked: boolean) {
    try {
      await apiFetch(`/shopping-lists/${listId}/items/${itemId}`, {
        method: "PATCH",
        body: JSON.stringify({ checked: !currentChecked }),
      });
      loadLists();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Konnte nicht aktualisiert werden");
    }
  }

  return (
    <>
      <h1>Einkauf</h1>
      <p className="subtitle">Einkaufslisten aus Essensplan. Abhaken, wenn gekauft.</p>

      {error && <div className="error-banner">{error}</div>}

      {lists.length === 0 ? (
        <p className="empty-state">Noch keine Einkaufslisten erstellt.</p>
      ) : (
        lists.map((list) => (
          <div key={list.id} className="card">
            <div className="icon-chip leaf" aria-hidden="true">
              🛒
            </div>
            <div className="card-body">
              <div className="card-title">Einkaufsliste · {list.status === "done" ? "✓ erledigt" : "offen"}</div>
              <div className="card-meta" style={{ marginBottom: 12 }}>
                Quelle: {list.source === "meal_plan" ? "Essensplan" : "Manuell"}
              </div>

              {list.items.length === 0 ? (
                <div style={{ color: "var(--text-muted)", fontSize: "13.5px" }}>Keine Einträge</div>
              ) : (
                <div>
                  {list.items.map((item) => (
                    <div
                      key={item.id}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "8px",
                        padding: "8px 0",
                        borderBottom: "1px solid var(--border)",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={item.checked}
                        onChange={() => toggleItem(list.id, item.id, item.checked)}
                        style={{ width: "auto", marginBottom: 0, cursor: "pointer" }}
                      />
                      <label
                        style={{
                          flex: 1,
                          cursor: "pointer",
                          textDecoration: item.checked ? "line-through" : "none",
                          color: item.checked ? "var(--text-muted)" : "var(--text)",
                        }}
                      >
                        {item.ingredient_name} · {item.quantity} {item.unit}
                      </label>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))
      )}
    </>
  );
}
