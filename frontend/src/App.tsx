import { type ReactElement, useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { clearToken, decodeRoleFromToken, getToken, isDevicePaired } from "./api/client";
import Calendar from "./pages/Calendar";
import ChildLogin from "./pages/ChildLogin";
import Children from "./pages/Children";
import Dashboard from "./pages/Dashboard";
import Documents from "./pages/Documents";
import Earnings from "./pages/Earnings";
import ForgotPassword from "./pages/ForgotPassword";
import Login from "./pages/Login";
import MealPlan from "./pages/MealPlan";
import Mehr from "./pages/Mehr";
import Profile from "./pages/Profile";
import RecipeFinder from "./pages/RecipeFinder";
import Recipes from "./pages/Recipes";
import Register from "./pages/Register";
import ResetPassword from "./pages/ResetPassword";
import Rewards from "./pages/Rewards";
import ShoppingList from "./pages/ShoppingList";
import Tasks from "./pages/Tasks";
import Timetable from "./pages/Timetable";
import { isParentRole } from "./utils/rating";
import { computeWallZoom } from "./utils/wallZoom";

const CORE_NAV_ITEMS = [
  { to: "/", label: "Heute", icon: "🏠", end: true },
  { to: "/aufgaben", label: "Aufgaben", icon: "⭐" },
  { to: "/belohnungen", label: "Belohnungen", icon: "🎁" },
  { to: "/essensplan", label: "Essen", icon: "🍽️" }
];

const CHILD_NAV_ITEMS = [...CORE_NAV_ITEMS, { to: "/ich", label: "Ich", icon: "🙂" }];
const PARENT_NAV_ITEMS = [...CORE_NAV_ITEMS, { to: "/mehr", label: "Mehr", icon: "⋯" }];
const DASHBOARD_NAV_ITEMS = [
  { to: "/", label: "Heute", icon: "🏠", end: true },
  { to: "/aufgaben", label: "Aufgaben", icon: "⭐" },
  { to: "/essensplan", label: "Essen", icon: "🍽️" },
];

/** Management-only pages (children, wall-dashboard account, adults): parents only, everyone else goes home
 *  before the page mounts, so no member data is ever requested. */
function RequireParent({ children }: { children: ReactElement }) {
  return isParentRole(decodeRoleFromToken(getToken() ?? "")) ? children : <Navigate to="/" replace />;
}

/** Wall tablet only: shows what the kiosk browser really reports, so the layout can be tuned to it. */
function DisplayInfo() {
  const [size, setSize] = useState({ w: window.innerWidth, h: window.innerHeight });
  useEffect(() => {
    const update = () => setSize({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);
  return (
    <p className="display-info">
      Anzeige {size.w}×{size.h} px · Skalierung ×{computeWallZoom(size.w)} · Pixeldichte {window.devicePixelRatio}
    </p>
  );
}

export default function App() {
  const navigate = useNavigate();
  const role = decodeRoleFromToken(getToken() ?? "");
  const navItems = role === "child" ? CHILD_NAV_ITEMS : role === "dashboard" ? DASHBOARD_NAV_ITEMS : PARENT_NAV_ITEMS;

  function switchToPinLogin() {
    clearToken();
    navigate(isDevicePaired() ? "/kind-login" : "/login");
  }

  function logoutToParentLogin() {
    clearToken();
    navigate("/login");
  }

  // Children and wall dashboards get a bright, always-light theme; parents keep following the system setting.
  document.documentElement.dataset.theme = role === "child" || role === "dashboard" ? "light" : "";

  // Wall tablet: scale the UI to the (often very wide) kiosk viewport so the whole screen is used.
  useEffect(() => {
    const root = document.documentElement;
    if (role !== "dashboard") {
      delete root.dataset.role;
      root.style.removeProperty("--ui-zoom");
      return;
    }
    root.dataset.role = "dashboard";
    const apply = () => root.style.setProperty("--ui-zoom", String(computeWallZoom(window.innerWidth)));
    apply();
    window.addEventListener("resize", apply);
    return () => window.removeEventListener("resize", apply);
  }, [role]);

  return (
    <div className="app-shell">
      <main className="app-main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/kinder" element={<RequireParent><Children /></RequireParent>} />
          <Route path="/kalender" element={<Calendar />} />
          <Route path="/stundenplan" element={<Timetable />} />
          <Route path="/dokumente" element={<RequireParent><Documents /></RequireParent>} />
          <Route path="/essensplan" element={<MealPlan />} />
          <Route path="/rezepte" element={<Recipes />} />
          <Route path="/rezepte/finden" element={<RecipeFinder />} />
          <Route path="/einkaufsliste" element={<ShoppingList />} />
          <Route path="/aufgaben" element={<Tasks />} />
          <Route path="/belohnungen" element={<Rewards />} />
          <Route path="/verdienst" element={<Earnings />} />
          <Route path="/mehr" element={<Mehr />} />
          <Route path="/ich" element={<Profile />} />
          <Route path="/login" element={<Login />} />
          <Route path="/passwort-vergessen" element={<ForgotPassword />} />
          <Route path="/passwort-zuruecksetzen" element={<ResetPassword />} />
          <Route path="/registrieren" element={<Register />} />
          <Route path="/kind-login" element={<ChildLogin />} />
        </Routes>
        {role === "dashboard" && <DisplayInfo />}
      </main>
      <nav className="app-nav">
        {navItems.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end}>
            <span className="icon" aria-hidden="true">{item.icon}</span>
            <span>{item.label}</span>
          </NavLink>
        ))}
        {role === "dashboard" && (
          <>
            {isDevicePaired() && (
              <button type="button" className="app-nav-action" onClick={switchToPinLogin}>
                <span className="icon" aria-hidden="true">👤</span>
                <span>Kind</span>
              </button>
            )}
            <button type="button" className="app-nav-action" onClick={logoutToParentLogin}>
              <span className="icon" aria-hidden="true">↪</span>
              <span>Abmelden</span>
            </button>
          </>
        )}
      </nav>
    </div>
  );
}
