import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import RecipeFinder from "./RecipeFinder";

const members = [
  { user_id: "p1", display_name: "Papa", likes: ["Reis"], dislikes: [], allergies: [] },
  { user_id: "c1", display_name: "Anna", likes: ["Nudeln", "Reis"], dislikes: ["Pilze"], allergies: ["Nüsse"] }
];

const proven = {
  id: "rec1",
  title: "Spaghetti",
  image_url: "https://img.example/a.jpg",
  prep_minutes: 25,
  source_name: "Family Recipe",
  source_url: null,
  ingredients: ["Nudeln", "Tomaten"],
  age_ok: true,
  age_hint: "",
  balanced: true,
  balance_label: "Ausgewogen",
  avg_stars: 4.33,
  rating_count: 3
};

const fresh = {
  candidate_id: "cand1",
  title: "Gemüsecurry",
  image_url: "http://insecure.example/b.jpg",
  prep_minutes: 40,
  source_name: "Kochbuch",
  source_url: "https://kochbuch.example/curry",
  ingredients: ["Reis", "Kürbis"],
  age_ok: true,
  age_hint: "Für Kleinkinder klein schneiden",
  balanced: false,
  balance_label: "Wenig Gemüse",
  avg_stars: null,
  rating_count: 0
};

function ok(data: unknown, status = 200) {
  return Promise.resolve({ ok: true, status, json: async () => data });
}

let calls: { url: string; method: string; body: unknown }[];

function stubApi(discover: () => Promise<unknown>) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, options?: RequestInit) => {
      const method = options?.method ?? "GET";
      calls.push({ url, method, body: options?.body ? JSON.parse(options.body as string) : undefined });
      if (url.endsWith("/food-preferences/")) return ok(members);
      if (url.endsWith("/families/children")) return ok([{ id: "c1", avatar_key: "🦊" }]);
      if (url.endsWith("/recipes/discover")) return discover();
      if (url.endsWith("/recipes/discover/adopt")) return ok({ recipe_id: "r9", created: true, meal_plan_entry_id: "e1" }, 201);
      if (url.endsWith("/meal-plan/entries")) return ok({ id: "e2" }, 201);
      return Promise.reject(new Error(`Unknown endpoint: ${url}`));
    })
  );
}

function renderFinder() {
  return render(
    <MemoryRouter>
      <RecipeFinder />
    </MemoryRouter>
  );
}

beforeEach(() => {
  const payload = btoa(JSON.stringify({ family_id: "fam1", role: "parent_admin", sub: "p1" }));
  localStorage.setItem("familienplaner_token", `eyJhbGciOiJIUzI1NiJ9.${payload}.test`);
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

async function selectAnnaAndSearch() {
  fireEvent.click(await screen.findByRole("button", { name: /Anna/ }));
  fireEvent.click(screen.getByRole("button", { name: "Rezepte suchen" }));
}

describe("RecipeFinder", () => {
  it("verlangt mindestens eine essende Person", async () => {
    stubApi(() => ok({ proven: [], discover: [] }));
    renderFinder();

    await screen.findByRole("button", { name: /Anna/ });
    expect(screen.getByRole("button", { name: "Rezepte suchen" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /Anna/ }));
    expect(screen.getByRole("button", { name: "Rezepte suchen" })).toBeEnabled();
  });

  it("zeigt Allergie-Hinweis und Lieblingszutaten der Gewählten", async () => {
    stubApi(() => ok({ proven: [], discover: [] }));
    renderFinder();

    fireEvent.click(await screen.findByRole("button", { name: /Anna/ }));

    expect(screen.getByRole("note")).toHaveTextContent("Beachtet: Allergie Nüsse (Anna)");
    const liked = screen.getByRole("list", { name: "Beliebte Zutaten" });
    expect(within(liked).getByText("Nudeln")).toBeInTheDocument();
    expect(within(liked).getByText("Reis")).toBeInTheDocument();
  });

  it("sendet Essende, Mahlzeit und Wunschzutaten", async () => {
    stubApi(() => ok({ proven: [], discover: [] }));
    renderFinder();

    fireEvent.click(await screen.findByRole("button", { name: /Anna/ }));
    fireEvent.change(screen.getByLabelText("Mahlzeit"), { target: { value: "lunch" } });
    const extra = screen.getByLabelText("Wunschzutat");
    fireEvent.change(extra, { target: { value: "Zucchini" } });
    fireEvent.submit(extra.closest("form")!);
    fireEvent.click(screen.getByRole("button", { name: "Rezepte suchen" }));

    await screen.findByText(/Keine passenden Rezepte gefunden/);
    const search = calls.find((c) => c.url.endsWith("/recipes/discover"))!;
    expect(search.method).toBe("POST");
    expect(search.body).toEqual({ eater_user_ids: ["c1"], meal_type: "lunch", extra_ingredients: ["Zucchini"] });
  });

  it("zeigt Skeleton während der Suche", async () => {
    let resolve: (v: unknown) => void = () => {};
    stubApi(() => new Promise((r) => (resolve = () => r({ ok: true, status: 200, json: async () => ({ proven: [], discover: [] }) }))));
    renderFinder();

    await selectAnnaAndSearch();
    expect(screen.getByLabelText("Rezepte werden gesucht")).toHaveAttribute("aria-busy", "true");
    resolve(null);
    await screen.findByText(/Keine passenden Rezepte gefunden/);
  });

  it("rendert beide Abschnitte mit deutscher Bewertung, Badges und sicheren Bildern", async () => {
    stubApi(() => ok({ proven: [proven], discover: [fresh] }));
    const { container } = renderFinder();

    await selectAnnaAndSearch();

    const provenSection = await screen.findByRole("region", { name: "Schon getestet" });
    expect(within(provenSection).getByText("⭐ 4,3 (3)")).toBeInTheDocument();
    expect(within(provenSection).getByText("Spaghetti")).toBeInTheDocument();
    const freshSection = screen.getByRole("region", { name: "Neu entdecken" });
    expect(within(freshSection).getByText("Gemüsecurry")).toBeInTheDocument();
    expect(within(freshSection).getByText(/Für Kleinkinder klein schneiden/)).toBeInTheDocument();
    expect(within(freshSection).getByText(/Wenig Gemüse/)).toBeInTheDocument();
    expect(within(freshSection).getByText("Kochbuch")).toBeInTheDocument();

    // https image is used with no-referrer, http image falls back to placeholder
    const images = container.querySelectorAll("img");
    expect(images).toHaveLength(1);
    expect(images[0]).toHaveAttribute("referrerpolicy", "no-referrer");
    expect(images[0]).toHaveAttribute("loading", "lazy");
  });

  it("zeigt Fehler bei fehlgeschlagener Suche", async () => {
    stubApi(() => Promise.resolve({ ok: false, status: 500, statusText: "x", json: async () => ({ detail: "Suche nicht verfügbar" }) }));
    renderFinder();

    await selectAnnaAndSearch();

    expect(await screen.findByRole("alert")).toHaveTextContent("Suche nicht verfügbar");
  });

  it("übernimmt einen neuen Vorschlag per adopt inklusive Datum und Mahlzeit", async () => {
    stubApi(() => ok({ proven: [proven], discover: [fresh] }));
    renderFinder();

    await selectAnnaAndSearch();
    fireEvent.click(await screen.findByRole("button", { name: /Gemüsecurry/ }));

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Kürbis")).toBeInTheDocument();
    const link = within(dialog).getByRole("link", { name: /Kochbuch/ });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");

    fireEvent.change(within(dialog).getByLabelText("Datum"), { target: { value: "2026-10-01" } });
    fireEvent.change(within(dialog).getByLabelText("Mahlzeit"), { target: { value: "dinner" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Einplanen" }));

    await within(dialog).findByText(/Eingeplant!/);
    expect(within(dialog).getByRole("link", { name: /Zum Essensplan/ })).toHaveAttribute("href", "/essensplan");
    const adopt = calls.find((c) => c.url.endsWith("/recipes/discover/adopt"))!;
    expect(adopt.body).toEqual({ candidate_id: "cand1", plan_date: "2026-10-01", meal_type: "dinner" });
  });

  it("plant bewährte Rezepte direkt über den Essensplan ein", async () => {
    stubApi(() => ok({ proven: [proven], discover: [] }));
    renderFinder();

    await selectAnnaAndSearch();
    fireEvent.click(await screen.findByRole("button", { name: /Spaghetti/ }));
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Datum"), { target: { value: "2026-10-02" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Einplanen" }));

    await within(dialog).findByText(/Eingeplant!/);
    const entry = calls.find((c) => c.url.endsWith("/meal-plan/entries"))!;
    expect(entry.body).toMatchObject({ date: "2026-10-02", recipe_id: "rec1" });
    expect(calls.some((c) => c.url.endsWith("/recipes/discover/adopt"))).toBe(false);
  });

  it("schliesst den Dialog mit Esc", async () => {
    stubApi(() => ok({ proven: [proven], discover: [] }));
    renderFinder();

    await selectAnnaAndSearch();
    fireEvent.click(await screen.findByRole("button", { name: /Spaghetti/ }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("meldet abgelaufene Vorschläge verständlich", async () => {
    stubApi(() => ok({ proven: [], discover: [fresh] }));
    renderFinder();
    await selectAnnaAndSearch();
    fireEvent.click(await screen.findByRole("button", { name: /Gemüsecurry/ }));

    const original = globalThis.fetch as unknown as (url: string, o?: RequestInit) => Promise<unknown>;
    vi.stubGlobal("fetch", (url: string, o?: RequestInit) =>
      url.endsWith("/discover/adopt")
        ? Promise.resolve({ ok: false, status: 404, statusText: "x", json: async () => ({ detail: "Candidate not found or expired" }) })
        : original(url, o)
    );
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Einplanen" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("abgelaufen");
  });

  it("kennzeichnet englische Rezepte", async () => {
    stubApi(() => ok({ proven: [], discover: [{ ...fresh, language: "en" }] }));
    renderFinder();

    await selectAnnaAndSearch();

    expect(await screen.findByText(/Englisch/)).toBeInTheDocument();
  });

  it("zeigt den Allergie-Hinweis unter den Ergebnissen", async () => {
    stubApi(() => ok({ proven: [proven], discover: [fresh] }));
    renderFinder();

    await selectAnnaAndSearch();

    expect(await screen.findByText(/Der Allergie-Filter ist eine Hilfe/)).toBeInTheDocument();
  });

  it("meldet nicht erreichbare Quellen statt 'nichts gefunden'", async () => {
    stubApi(() =>
      ok({ proven: [], discover: [], source_warnings: ["Chefkoch.de war nicht erreichbar."] })
    );
    renderFinder();

    await selectAnnaAndSearch();

    expect(await screen.findByText("Chefkoch.de war nicht erreichbar.")).toBeInTheDocument();
    expect(screen.getByText(/Es konnten keine Rezepte geladen werden/)).toBeInTheDocument();
    expect(screen.queryByText(/Keine passenden Rezepte gefunden/)).not.toBeInTheDocument();
  });

  it("meldet bei bewährten Rezepten 'Rezept nicht gefunden' statt 'abgelaufen'", async () => {
    stubApi(() => ok({ proven: [proven], discover: [] }));
    renderFinder();
    await selectAnnaAndSearch();
    fireEvent.click(await screen.findByRole("button", { name: /Spaghetti/ }));

    const original = globalThis.fetch as unknown as (url: string, o?: RequestInit) => Promise<unknown>;
    vi.stubGlobal("fetch", (url: string, o?: RequestInit) =>
      url.endsWith("/meal-plan/entries")
        ? Promise.resolve({ ok: false, status: 404, statusText: "x", json: async () => ({ detail: "Recipe not found" }) })
        : original(url, o)
    );
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Einplanen" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Rezept nicht gefunden");
    expect(alert).not.toHaveTextContent("abgelaufen");
  });

  it("plant standardmässig für das lokale Datum (kurz nach lokaler Mitternacht)", async () => {
    vi.stubEnv("TZ", "Europe/Zurich");
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 6, 15, 0, 30));
    try {
      stubApi(() => ok({ proven: [proven], discover: [] }));
      renderFinder();
      await selectAnnaAndSearch();
      fireEvent.click(await screen.findByRole("button", { name: /Spaghetti/ }));
      expect(within(screen.getByRole("dialog")).getByLabelText("Datum")).toHaveValue("2026-07-15");
    } finally {
      vi.useRealTimers();
      vi.unstubAllEnvs();
    }
  });
});
