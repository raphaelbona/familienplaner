import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { apiFetch, clearToken, decodeRoleFromToken, getToken } from "../api/client";

type MehrLink = {
  to: string;
  label: string;
  icon: string;
  iconClass?: string;
};

type HealthResponse = {
  status: string;
  hosting_mode: string;
};

const PLANNING_LINKS: MehrLink[] = [
  { to: "/kalender", label: "Kalender", icon: "📅", iconClass: "leaf" },
  { to: "/stundenplan", label: "Stundenplan", icon: "📚", iconClass: "sky" },
  { to: "/rezepte", label: "Rezepte", icon: "📖", iconClass: "leaf" },
  { to: "/rezepte/finden", label: "Rezepte finden", icon: "🔍", iconClass: "leaf" },
  { to: "/einkaufsliste", label: "Einkaufsliste", icon: "🛒" }
];

const ADMIN_LINKS: MehrLink[] = [
  { to: "/kinder", label: "Kinder verwalten", icon: "🧒" },
  { to: "/dokumente", label: "Briefe", icon: "📄", iconClass: "sky" },
  { to: "/verdienst", label: "Verdienst", icon: "💰", iconClass: "leaf" }
];

function MehrLinkCard({ to, label, icon, iconClass }: MehrLink) {
  return (
    <Link to={to} className="card" style={{ textDecoration: "none", color: "inherit" }}>
      <div className={`icon-chip ${iconClass ?? ""}`} aria-hidden="true">
        {icon}
      </div>
      <div className="card-body" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div className="card-title" style={{ marginBottom: 0 }}>
          {label}
        </div>
        <span aria-hidden="true" style={{ color: "var(--text-muted)" }}>
          →
        </span>
      </div>
    </Link>
  );
}

export default function Mehr() {
  const navigate = useNavigate();
  const role = decodeRoleFromToken(getToken() ?? "");
  const isLoggedIn = role !== null;
  const isParent = role === "parent_admin" || role === "parent_member";
  const [health, setHealth] = useState<HealthResponse | null>(null);

  // "Mehr" is the parent/admin area; children and the wall dashboard navigating here
  // directly (the bottom nav never links to it for them) get sent back to Heute.
  useEffect(() => {
    if (role === "child" || role === "dashboard") navigate("/", { replace: true });
  }, [role, navigate]);

  useEffect(() => {
    if (!isParent) return;
    apiFetch<HealthResponse>("/health").then(setHealth).catch(() => setHealth(null));
  }, [isParent]);

  function handleLogout() {
    // Deliberately keep family_id in localStorage: it's public (used only to fetch the
    // shared-tablet avatar picker at /kind-login) and clearing it here would strand a
    // family's own device unable to even show its children's login screen anymore.
    clearToken();
    navigate("/login");
  }

  if (role === "child" || role === "dashboard") return null;

  return (
    <>
      <h1>Mehr</h1>
      <p className="subtitle">Planung, Verwaltung und Konto.</p>

      <h2 style={{ fontSize: "15px", marginBottom: 10 }}>Planung</h2>
      {PLANNING_LINKS.map((item) => (
        <MehrLinkCard key={item.to} {...item} />
      ))}

      <h2 style={{ fontSize: "15px", margin: "22px 0 10px" }}>Verwaltung</h2>
      {ADMIN_LINKS.map((item) => (
        <MehrLinkCard key={item.to} {...item} />
      ))}

      <h2 style={{ fontSize: "15px", margin: "22px 0 10px" }}>Konto</h2>
      {isLoggedIn ? (
        <button type="button" className="secondary" onClick={handleLogout} style={{ width: "100%" }}>
          Abmelden
        </button>
      ) : (
        <Link to="/login" className="btn" style={{ display: "block", textAlign: "center", textDecoration: "none" }}>
          Anmelden
        </Link>
      )}

      {isParent && health && (
        <p className="card-meta" style={{ marginTop: 24, textAlign: "center" }}>
          System: {health.status} · Hosting: {health.hosting_mode}
        </p>
      )}
    </>
  );
}
