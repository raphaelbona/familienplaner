import { FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiFetch, decodeRoleFromToken, getToken } from "../api/client";
import { FamilyMember, fetchFamilyMembers } from "../api/members";
import DevicesCard from "../components/DevicesCard";
import FoodPreferences from "../components/FoodPreferences";
import { AVATAR_NAMES, AVATAR_OPTIONS } from "../constants/avatars";

interface Child {
  id: string;
  display_name: string;
  birth_year: number | null;
  avatar_key: string | null;
  pin_locked_until?: string | null;
  color_index?: number;
}

interface DashboardUser {
  id: string;
  display_name: string;
  avatar_key: string | null;
}

interface PointsBalance {
  balance: number;
}

interface EarningsBalance {
  pending_approval: number;
  approved: number;
}

interface ChildWithBalances extends Child {
  points: number;
  earningsPending: number;
  earningsApproved: number;
}

const DASHBOARD_AVATARS = ["📋", "🏠", "📺", "🗓️", "✅", "⭐"];

export default function Children() {
  const navigate = useNavigate();
  const token = getToken();
  const role = token ? decodeRoleFromToken(token) : null;

  // Role check: only parents can access this page
  useEffect(() => {
    if (role && !["parent_admin", "parent_member"].includes(role)) {
      navigate("/");
    }
  }, [role, navigate]);

  const [children, setChildren] = useState<ChildWithBalances[]>([]);
  const [dashboardUser, setDashboardUser] = useState<DashboardUser | null>(null);
  const [members, setMembers] = useState<FamilyMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Create form state
  const [newChildName, setNewChildName] = useState("");
  const [newChildBirthYear, setNewChildBirthYear] = useState("");
  const [newChildPin, setNewChildPin] = useState("");
  const [newChildAvatar, setNewChildAvatar] = useState(AVATAR_OPTIONS[0]);
  const [creatingChild, setCreatingChild] = useState(false);

  // Wall dashboard form state
  const [dashboardName, setDashboardName] = useState("Wand-Dashboard");
  const [dashboardPin, setDashboardPin] = useState("");
  const [dashboardAvatar, setDashboardAvatar] = useState("📋");
  const [savingDashboard, setSavingDashboard] = useState(false);

  // Edit form state
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editBirthYear, setEditBirthYear] = useState("");
  const [editPin, setEditPin] = useState("");
  const [editAvatar, setEditAvatar] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);

  async function loadChildren() {
    setLoading(true);
    setError(null);
    try {
      const childrenData = await apiFetch<Child[]>("/families/children");
      const dashboardData = await apiFetch<DashboardUser | null>("/families/dashboard-user");
      setMembers(await fetchFamilyMembers().catch(() => []));
      setDashboardUser(dashboardData);
      setDashboardName(dashboardData?.display_name ?? "Wand-Dashboard");
      setDashboardAvatar(dashboardData?.avatar_key ?? "📋");
      setDashboardPin("");

      // Fetch balances for each child
      const childrenWithBalances: ChildWithBalances[] = [];

      for (const child of childrenData) {
        try {
          const pointsRes = await apiFetch<PointsBalance>("/points/balance", {
            method: "GET",
          });
          const points = pointsRes.balance || 0;

          const earningsRes = await apiFetch<EarningsBalance>("/earnings/balance", {
            method: "GET",
          });
          const earningsPending = earningsRes.pending_approval || 0;
          const earningsApproved = earningsRes.approved || 0;

          childrenWithBalances.push({
            ...child,
            points,
            earningsPending,
            earningsApproved,
          });
        } catch (err) {
          // If balance fetch fails, just show the child without balance
          childrenWithBalances.push({
            ...child,
            points: 0,
            earningsPending: 0,
            earningsApproved: 0,
          });
        }
      }

      setChildren(childrenWithBalances);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Fehler beim Laden der Kinder");
    } finally {
      setLoading(false);
    }
  }

  async function handleSaveDashboard(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSavingDashboard(true);

    try {
      const payload: Record<string, string> = {
        display_name: dashboardName,
        avatar_key: dashboardAvatar,
      };

      if (dashboardPin) {
        payload.pin = dashboardPin;
      }

      const saved = await apiFetch<DashboardUser>("/families/dashboard-user", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      setDashboardUser(saved);
      setDashboardPin("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Wand-Dashboard konnte nicht gespeichert werden");
    } finally {
      setSavingDashboard(false);
    }
  }

  useEffect(() => {
    loadChildren();
  }, []);

  async function handleCreateChild(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setCreatingChild(true);

    try {
      const birthYear = newChildBirthYear ? parseInt(newChildBirthYear, 10) : null;
      await apiFetch("/families/children", {
        method: "POST",
        body: JSON.stringify({
          display_name: newChildName,
          birth_year: birthYear,
          pin: newChildPin,
          avatar_key: newChildAvatar,
        }),
      });

      setNewChildName("");
      setNewChildBirthYear("");
      setNewChildPin("");
      setNewChildAvatar(AVATAR_OPTIONS[0]);
      await loadChildren();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kind konnte nicht erstellt werden");
    } finally {
      setCreatingChild(false);
    }
  }

  async function handleUpdateChild(childId: string) {
    setSavingEdit(true);
    setError(null);

    try {
      const birthYear = editBirthYear ? parseInt(editBirthYear, 10) : null;
      const updateData: Record<string, unknown> = {
        display_name: editName,
      };

      if (birthYear !== null) {
        updateData.birth_year = birthYear;
      }

      if (editAvatar) {
        updateData.avatar_key = editAvatar;
      }

      if (editPin) {
        updateData.pin = editPin;
      }

      await apiFetch(`/families/children/${childId}`, {
        method: "PATCH",
        body: JSON.stringify(updateData),
      });

      setEditingId(null);
      await loadChildren();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kind konnte nicht aktualisiert werden");
    } finally {
      setSavingEdit(false);
    }
  }

  async function handleDeleteChild(childId: string, childName: string) {
    const confirmed = window.confirm(
      `Willst du "${childName}" wirklich entfernen? Dies kann nicht rückgängig gemacht werden.`
    );

    if (!confirmed) return;

    setError(null);

    try {
      await apiFetch(`/families/children/${childId}`, {
        method: "DELETE",
      });

      await loadChildren();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kind konnte nicht gelöscht werden");
    }
  }

  async function handleUnlockChild(childId: string) {
    setError(null);
    try {
      await apiFetch(`/families/children/${childId}/unlock`, { method: "POST" });
      await loadChildren();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kind konnte nicht entsperrt werden");
    }
  }

  function startEditing(child: ChildWithBalances) {
    setEditingId(child.id);
    setEditName(child.display_name);
    setEditBirthYear(child.birth_year?.toString() || "");
    setEditPin("");
    setEditAvatar(child.avatar_key || "");
  }

  const childIds = new Set(children.map((c) => c.id));
  const adults = members.filter((m) => !childIds.has(m.id) && m.id !== dashboardUser?.id);

  if (loading) {
    return (
      <>
        <h1>Kinder</h1>
        <div className="form-card" style={{ textAlign: "center" }}>
          <p>Lädt…</p>
        </div>
      </>
    );
  }

  return (
    <>
      <h1>Kinder</h1>
      <p className="subtitle">Verwalte die Konten deiner Kinder.</p>

      {error && <div className="error-banner">{error}</div>}

      <DevicesCard />

      {/* Create new child form */}
      <div className="form-card">
        <h2 style={{ marginTop: 0, marginBottom: 20, fontSize: "18px" }}>Neues Kind</h2>
        <form onSubmit={handleCreateChild}>
          <label htmlFor="new-name">Name</label>
          <input
            id="new-name"
            type="text"
            value={newChildName}
            onChange={(e) => setNewChildName(e.target.value)}
            placeholder="z.B. Anna"
            required
          />

          <label htmlFor="new-birth-year">Geburtsjahr (optional)</label>
          <input
            id="new-birth-year"
            type="number"
            value={newChildBirthYear}
            onChange={(e) => setNewChildBirthYear(e.target.value)}
            placeholder="z.B. 2015"
            min={new Date().getFullYear() - 18}
            max={new Date().getFullYear()}
          />

          <label htmlFor="new-pin">PIN (4 Ziffern)</label>
          <input
            id="new-pin"
            type="text"
            inputMode="numeric"
            maxLength={4}
            value={newChildPin}
            onChange={(e) => setNewChildPin(e.target.value.replace(/\D/g, ""))}
            placeholder="1234"
            required
          />

          <label>Avatar</label>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(76px, 1fr))",
              gap: "0.6rem",
              marginBottom: "1rem",
            }}
          >
            {AVATAR_OPTIONS.map((emoji) => (
              <button
                key={emoji}
                type="button"
                onClick={() => setNewChildAvatar(emoji)}
                aria-label={AVATAR_NAMES[emoji]}
                aria-pressed={newChildAvatar === emoji}
                style={{
                  border: newChildAvatar === emoji ? "3px solid var(--accent)" : "1.5px solid var(--border)",
                  background: newChildAvatar === emoji ? "var(--accent-soft)" : "var(--surface-2)",
                  borderRadius: "14px",
                  padding: "10px 6px",
                  minHeight: "76px",
                  cursor: "pointer",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: "4px",
                }}
              >
                <span style={{ fontSize: "2rem", lineHeight: 1 }} aria-hidden="true">
                  {emoji}
                </span>
                <span style={{ fontSize: "11.5px", fontWeight: 600, color: "var(--text-muted)" }}>
                  {AVATAR_NAMES[emoji]}
                </span>
              </button>
            ))}
          </div>

          <button type="submit" disabled={creatingChild || !newChildName || !newChildPin}>
            {creatingChild ? "Wird erstellt…" : "Kind erstellen"}
          </button>
        </form>
      </div>

      <div className="form-card">
        <h2 style={{ marginTop: 0, marginBottom: 8, fontSize: "18px" }}>Wand-Dashboard</h2>
        <p className="card-meta" style={{ margin: "0 0 16px 0" }}>
          Für das Tablet an der Wand: zeigt die Familie und kann Aufgaben für alle abhaken.
        </p>
        <form onSubmit={handleSaveDashboard}>
          <label htmlFor="dashboard-name">Name</label>
          <input
            id="dashboard-name"
            type="text"
            value={dashboardName}
            onChange={(e) => setDashboardName(e.target.value)}
            required
          />

          <label htmlFor="dashboard-pin">
            {dashboardUser ? "Neue PIN (leer = nicht ändern)" : "PIN (4 Ziffern)"}
          </label>
          <input
            id="dashboard-pin"
            type="text"
            inputMode="numeric"
            maxLength={4}
            value={dashboardPin}
            onChange={(e) => setDashboardPin(e.target.value.replace(/\D/g, ""))}
            placeholder="1234"
            required={!dashboardUser}
          />

          <label>Icon</label>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "0.5rem",
              marginBottom: "1rem",
            }}
          >
            {DASHBOARD_AVATARS.map((emoji) => (
              <button
                key={emoji}
                type="button"
                onClick={() => setDashboardAvatar(emoji)}
                aria-pressed={dashboardAvatar === emoji}
                style={{
                  width: 52,
                  height: 52,
                  border: dashboardAvatar === emoji ? "3px solid var(--accent)" : "1.5px solid var(--border)",
                  background: dashboardAvatar === emoji ? "var(--accent-soft)" : "var(--surface-2)",
                  borderRadius: "14px",
                  padding: 0,
                  minHeight: 52,
                  fontSize: "1.5rem",
                }}
              >
                {emoji}
              </button>
            ))}
          </div>

          <button
            type="submit"
            disabled={
              savingDashboard ||
              !dashboardName ||
              (!dashboardUser && dashboardPin.length !== 4) ||
              (dashboardPin.length > 0 && dashboardPin.length !== 4)
            }
          >
            {savingDashboard
              ? "Wird gespeichert…"
              : dashboardUser
                ? "Wand-Dashboard speichern"
                : "Wand-Dashboard erstellen"}
          </button>
        </form>
      </div>

      {/* Children list */}
      {children.length === 0 ? (
        <p className="empty-state">Noch keine Kinder erfasst.</p>
      ) : (
        children.map((child) => (
          <div key={child.id} className="card">
            {editingId === child.id ? (
              // Edit form
              <div style={{ width: "100%" }}>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    handleUpdateChild(child.id);
                  }}
                >
                  <label htmlFor={`edit-name-${child.id}`}>Name</label>
                  <input
                    id={`edit-name-${child.id}`}
                    type="text"
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    required
                  />

                  <label htmlFor={`edit-birth-year-${child.id}`}>Geburtsjahr</label>
                  <input
                    id={`edit-birth-year-${child.id}`}
                    type="number"
                    value={editBirthYear}
                    onChange={(e) => setEditBirthYear(e.target.value)}
                    min={new Date().getFullYear() - 18}
                    max={new Date().getFullYear()}
                  />

                  <label htmlFor={`edit-pin-${child.id}`}>Neue PIN (leer = nicht ändern)</label>
                  <input
                    id={`edit-pin-${child.id}`}
                    type="text"
                    inputMode="numeric"
                    maxLength={4}
                    value={editPin}
                    onChange={(e) => setEditPin(e.target.value.replace(/\D/g, ""))}
                    placeholder="1234"
                  />

                  <label>Avatar</label>
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(auto-fit, minmax(76px, 1fr))",
                      gap: "0.6rem",
                      marginBottom: "1rem",
                    }}
                  >
                    {AVATAR_OPTIONS.map((emoji) => (
                      <button
                        key={emoji}
                        type="button"
                        onClick={() => setEditAvatar(emoji)}
                        aria-label={AVATAR_NAMES[emoji]}
                        aria-pressed={editAvatar === emoji}
                        style={{
                          border: editAvatar === emoji ? "3px solid var(--accent)" : "1.5px solid var(--border)",
                          background: editAvatar === emoji ? "var(--accent-soft)" : "var(--surface-2)",
                          borderRadius: "14px",
                          padding: "10px 6px",
                          minHeight: "76px",
                          cursor: "pointer",
                          display: "flex",
                          flexDirection: "column",
                          alignItems: "center",
                          justifyContent: "center",
                          gap: "4px",
                        }}
                      >
                        <span style={{ fontSize: "2rem", lineHeight: 1 }} aria-hidden="true">
                          {emoji}
                        </span>
                        <span style={{ fontSize: "11.5px", fontWeight: 600, color: "var(--text-muted)" }}>
                          {AVATAR_NAMES[emoji]}
                        </span>
                      </button>
                    ))}
                  </div>

                  <div style={{ display: "flex", gap: "0.5rem" }}>
                    <button type="submit" disabled={savingEdit || !editName}>
                      {savingEdit ? "Wird gespeichert…" : "Speichern"}
                    </button>
                    <button
                      type="button"
                      onClick={() => setEditingId(null)}
                      className="secondary"
                    >
                      Abbrechen
                    </button>
                  </div>
                </form>
              </div>
            ) : (
              // Display view
              <>
                <div className="icon-chip-lg" aria-hidden="true">
                  {child.avatar_key || "🧒"}
                </div>
                <div className="card-body">
                  <div className="card-title">{child.display_name}</div>
                  {child.birth_year && (
                    <div className="card-meta">
                      {new Date().getFullYear() - child.birth_year} Jahre alt
                    </div>
                  )}
                  {child.points > 0 && (
                    <div className="card-meta" style={{ marginTop: "0.5rem", color: "var(--accent)" }}>
                      ⭐ {child.points} Punkte
                    </div>
                  )}
                  {(child.earningsPending > 0 || child.earningsApproved > 0) && (
                    <div className="card-meta" style={{ marginTop: "0.25rem" }}>
                      {child.earningsPending > 0 && `💰 CHF ${child.earningsPending.toFixed(2)} ausstehend`}
                      {child.earningsPending > 0 && child.earningsApproved > 0 && " / "}
                      {child.earningsApproved > 0 && `CHF ${child.earningsApproved.toFixed(2)} freigegeben`}
                    </div>
                  )}

                  {child.pin_locked_until && new Date(child.pin_locked_until).getTime() > Date.now() && (
                    <div
                      className="card-meta"
                      style={{ marginTop: "0.5rem", display: "flex", gap: "0.5rem", alignItems: "center" }}
                    >
                      <span>
                        Gesperrt bis{" "}
                        {new Date(child.pin_locked_until).toLocaleTimeString("de-CH", {
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </span>
                      <button type="button" className="secondary" onClick={() => handleUnlockChild(child.id)}>
                        Entsperren
                      </button>
                    </div>
                  )}

                  <div style={{ marginTop: "1rem" }}>
                    <FoodPreferences userId={child.id} name={child.display_name} collapsible />
                  </div>

                  <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginTop: "1rem" }}>
                    <button onClick={() => startEditing(child)} style={{ flex: 1 }}>
                      Bearbeiten
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => handleDeleteChild(child.id, child.display_name)}
                      style={{
                        background: "transparent",
                        color: "var(--text-muted)",
                        minHeight: "auto",
                        padding: "6px 10px",
                        fontSize: "13px",
                        fontWeight: 500,
                      }}
                    >
                      Entfernen
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        ))
      )}

      {adults.length > 0 && (
        <>
          <h2 style={{ fontSize: "18px", margin: "26px 0 12px" }}>Erwachsene</h2>
          {adults.map((adult) => (
            <div key={adult.id} className="card">
              <div className="icon-chip-lg" aria-hidden="true">
                {adult.avatar_key || "🧑"}
              </div>
              <div className="card-body">
                <div className="card-title">{adult.display_name}</div>
                <div style={{ marginTop: "0.75rem" }}>
                  <FoodPreferences userId={adult.id} name={adult.display_name} collapsible />
                </div>
              </div>
            </div>
          ))}
        </>
      )}
    </>
  );
}
