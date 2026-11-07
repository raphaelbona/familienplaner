import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { apiFetch, clearToken, decodeUserIdFromToken, getFamilyId, getToken } from "../api/client";
import FoodPreferences from "../components/FoodPreferences";

type ChildAccount = {
  id: string;
  display_name: string;
  avatar_key: string | null;
};

type PointsBalance = {
  balance: number;
};

const MORE_LINKS = [
  { to: "/kalender", label: "Kalender", icon: "📅" },
  { to: "/stundenplan", label: "Stundenplan", icon: "📚" },
  { to: "/rezepte", label: "Rezepte", icon: "📖" },
  { to: "/einkaufsliste", label: "Einkaufsliste", icon: "🛒" },
  { to: "/verdienst", label: "Mein Taschengeld", icon: "💰" }
];

export default function Profile() {
  const navigate = useNavigate();
  const [me, setMe] = useState<ChildAccount | null>(null);
  const [points, setPoints] = useState<number | null>(null);
  const userId = decodeUserIdFromToken(getToken() ?? "");

  useEffect(() => {
    const token = getToken();
    const userId = token ? decodeUserIdFromToken(token) : null;
    const familyId = getFamilyId();

    if (userId && familyId) {
      apiFetch<ChildAccount[]>(`/families/${familyId}/child-accounts`)
        .then((accounts) => setMe(accounts.find((a) => a.id === userId) ?? null))
        .catch(() => setMe(null));
    }

    apiFetch<PointsBalance>("/points/balance")
      .then((data) => setPoints(data.balance))
      .catch(() => setPoints(null));
  }, []);

  function handleLogout() {
    // Keep family_id: it's public (drives the /kind-login avatar picker) and clearing
    // it would strand this device unable to show any child's login screen afterward.
    clearToken();
    navigate("/kind-login");
  }

  return (
    <>
      <h1>Ich</h1>

      <div className="card">
        <div className="icon-chip-lg" aria-hidden="true" style={{ marginBottom: 0, fontSize: "40px" }}>
          {me?.avatar_key ?? "🙂"}
        </div>
        <div className="card-body">
          <div className="card-title" style={{ fontSize: "20px" }}>
            {me?.display_name ?? "Du"}
          </div>
          {points !== null && <div className="card-meta">⭐ {points} Punkte gesammelt</div>}
        </div>
      </div>

      {userId && (
        <div style={{ marginTop: 22 }}>
          <FoodPreferences userId={userId} />
        </div>
      )}

      <h2 style={{ fontSize: "15px", margin: "22px 0 10px" }}>Mehr entdecken</h2>
      {MORE_LINKS.map((item) => (
        <Link key={item.to} to={item.to} className="card" style={{ textDecoration: "none", color: "inherit" }}>
          <div className="icon-chip" aria-hidden="true">
            {item.icon}
          </div>
          <div className="card-body" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div className="card-title" style={{ marginBottom: 0 }}>
              {item.label}
            </div>
            <span aria-hidden="true" style={{ color: "var(--text-muted)" }}>
              →
            </span>
          </div>
        </Link>
      ))}

      <button type="button" className="secondary" onClick={handleLogout} style={{ width: "100%", marginTop: 10 }}>
        Abmelden
      </button>
    </>
  );
}
