import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import MealPlan from "./MealPlan";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

function login(role: string) {
  const payload = btoa(JSON.stringify({ family_id: "fam1", role, sub: "u1" }));
  localStorage.setItem("familienplaner_token", `eyJhbGciOiJIUzI1NiJ9.${payload}.test`);
}

function stubApi() {
  const today = new Date().toISOString().slice(0, 10);
  const future = new Date(Date.now() + 5 * 86400000).toISOString().slice(0, 10);
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const ok = (data: unknown) => Promise.resolve({ ok: true, status: 200, json: async () => data });
      if (url.endsWith("/meal-plan/entries")) {
        return ok([
          { id: "e1", date: today, meal_type: "lunch", recipe_id: "r1", recipe_name: "Lasagne" },
          { id: "e2", date: future, meal_type: "dinner", recipe_id: "r2", recipe_name: "Suppe" }
        ]);
      }
      if (url.endsWith("/recipes/r1/ratings-summary")) return ok({ average: 4.25, rating_count: 4 });
      return Promise.reject(new Error(`Unknown endpoint: ${url}`));
    })
  );
}

function renderPage() {
  return render(
    <MemoryRouter>
      <MealPlan />
    </MemoryRouter>
  );
}

describe("MealPlan Bewertung", () => {
  it("bietet Bewerten nur für heutige oder vergangene Einträge und zeigt den Familien-Schnitt", async () => {
    login("parent_admin");
    stubApi();
    renderPage();

    expect(await screen.findByText("Familie: ⭐ 4,3 (4)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Lasagne bewerten" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Suppe bewerten" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Rezepte finden/ })).toHaveAttribute("href", "/rezepte/finden");
  });

  it("öffnet den Bewertungsdialog", async () => {
    login("child");
    stubApi();
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Lasagne bewerten" }));

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("radio", { name: "5 von 5 Sternen" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Rezepte finden/ })).not.toBeInTheDocument();
  });

  it("zeigt dem Wand-Dashboard keine Bewerten-Buttons", async () => {
    login("dashboard");
    stubApi();
    renderPage();

    await screen.findByText("Lasagne");
    expect(screen.queryByRole("button", { name: /bewerten/ })).not.toBeInTheDocument();
  });

  it("fragt Durchschnitte nur für die letzten 14 Tage ab, bietet Bewerten aber weiter an", async () => {
    login("parent_admin");
    const day = (offset: number) => {
      const d = new Date();
      d.setDate(d.getDate() + offset);
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    };
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        urls.push(url);
        const ok = (data: unknown) => Promise.resolve({ ok: true, status: 200, json: async () => data });
        if (url.endsWith("/meal-plan/entries")) {
          return ok([
            { id: "old", date: day(-60), meal_type: "lunch", recipe_id: "rOld", recipe_name: "Alt" },
            { id: "edge", date: day(-14), meal_type: "lunch", recipe_id: "rEdge", recipe_name: "Kante" },
            { id: "now", date: day(0), meal_type: "lunch", recipe_id: "rNow", recipe_name: "Heute" }
          ]);
        }
        if (url.includes("/ratings-summary")) return ok({ average: 3, rating_count: 1 });
        return Promise.reject(new Error(`Unknown endpoint: ${url}`));
      })
    );
    renderPage();

    await screen.findAllByText("Familie: ⭐ 3,0 (1)");
    const summaryUrls = urls.filter((u) => u.includes("ratings-summary"));
    expect(summaryUrls.some((u) => u.includes("/rOld/"))).toBe(false);
    expect(summaryUrls).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Alt bewerten" })).toBeInTheDocument();
  });

  it("aktualisiert nach einer Bewertung nur die Zusammenfassung des betroffenen Rezepts", async () => {
    login("child");
    const today = new Date();
    const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, options?: RequestInit) => {
        urls.push(`${options?.method ?? "GET"} ${url}`);
        const ok = (data: unknown) => Promise.resolve({ ok: true, status: 200, json: async () => data });
        if (url.endsWith("/meal-plan/entries")) {
          return ok([
            { id: "a", date: iso, meal_type: "lunch", recipe_id: "rA", recipe_name: "Lasagne" },
            { id: "b", date: iso, meal_type: "dinner", recipe_id: "rB", recipe_name: "Suppe" }
          ]);
        }
        if (url.includes("/ratings-summary")) return ok({ average: 3, rating_count: 1 });
        if (options?.method === "POST") return ok({});
        return Promise.reject(new Error(`Unknown endpoint: ${url}`));
      })
    );
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Lasagne bewerten" }));
    await waitFor(() => expect(urls.filter((u) => u.includes("ratings-summary"))).toHaveLength(2));

    fireEvent.click(screen.getByRole("radio", { name: "5 von 5 Sternen" }));
    fireEvent.click(screen.getByRole("button", { name: "Bewertung speichern" }));

    await screen.findByText("Danke für deine Bewertung!");
    await waitFor(() => expect(urls.filter((u) => u.includes("/rA/ratings-summary"))).toHaveLength(2));
    expect(urls.filter((u) => u.includes("/rB/ratings-summary"))).toHaveLength(1);
    expect(urls.filter((u) => u.endsWith("/meal-plan/entries"))).toHaveLength(1);
  });
});
