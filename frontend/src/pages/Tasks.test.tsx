import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Tasks from "./Tasks";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("Tasks", () => {
  it("aktualisiert 'Meine Aufgaben' sofort nach dem Zuweisen, ohne dass die Seite neu geladen werden muss", async () => {
    const payload = btoa(JSON.stringify({ family_id: "fam1", role: "parent_admin", sub: "parent1" }));
    localStorage.setItem("familienplaner_token", `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.${payload}.test`);
    localStorage.setItem("familienplaner_family_id", "fam1");

    let assigned = false;
    const today = new Date().toISOString().slice(0, 10);

    const fetchMock = vi.fn((url: string, options?: RequestInit) => {
      const method = options?.method ?? "GET";

      if (method === "POST" && url.includes("/tasks/instances")) {
        assigned = true;
        return Promise.resolve({ ok: true, status: 201, json: async () => ({}) });
      }
      if (url.includes("/tasks/instances?status_filter=completed")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => [] });
      }
      if (url.includes("/tasks/instances")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () =>
            assigned
              ? [
                  {
                    id: "i1",
                    assignee_user_id: "c1",
                    due_date: today,
                    status: "open",
                    template: { title: "Zimmer aufräumen", kind: "basis", points_value: 0, bonus_multiplier: 1 }
                  }
                ]
              : []
        });
      }
      if (url.includes("/tasks/templates")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => [
            { id: "t1", title: "Zimmer aufräumen", kind: "basis", points_value: 0, bonus_multiplier: 1, money_value_chf: 0 }
          ]
        });
      }
      if (url.includes("/families/fam1/child-accounts")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => [{ id: "c1", display_name: "Lea", avatar_key: null }] });
      }
      return Promise.reject(new Error(`Unknown endpoint: ${url} ${method}`));
    });

    vi.stubGlobal("fetch", fetchMock);

    render(<Tasks />);

    await waitFor(() => {
      expect(screen.getByText(/Heute ist frei/i)).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText(/Aufgaben-Vorlage/i), { target: { value: "t1" } });
    fireEvent.click(screen.getByLabelText(/^Lea$/i));
    fireEvent.change(screen.getByLabelText(/Fälligkeitsdatum/i), { target: { value: today } });
    fireEvent.click(screen.getByRole("button", { name: /^Zuweisen$/i }));

    await waitFor(() => {
      expect(screen.queryByText(/Heute ist frei/i)).not.toBeInTheDocument();
    });
  });

  it("zeigt zukünftige Aufgaben an, ohne sie vorzeitig abhakbar zu machen", async () => {
    const payload = btoa(JSON.stringify({ family_id: "fam1", role: "child", sub: "c1" }));
    localStorage.setItem("familienplaner_token", `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.${payload}.test`);
    localStorage.setItem("familienplaner_family_id", "fam1");

    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    const tomorrowIso = tomorrow.toISOString().slice(0, 10);

    const fetchMock = vi.fn((url: string) => {
      if (url.includes("/tasks/instances")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => [
            {
              id: "future-1",
              assignee_user_id: "c1",
              due_date: tomorrowIso,
              status: "open",
              template: {
                title: "Zimmer aufräumen",
                kind: "basis",
                points_value: 0,
                bonus_multiplier: 1,
                money_value_chf: 0,
                icon: null
              }
            }
          ]
        });
      }
      return Promise.reject(new Error(`Unknown endpoint: ${url}`));
    });

    vi.stubGlobal("fetch", fetchMock);

    render(<Tasks />);

    await waitFor(() => {
      expect(screen.getByText(/Alles geschafft/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("tab", { name: /Morgen/i }));

    await waitFor(() => {
      expect(screen.getByText("Zimmer aufräumen")).toBeInTheDocument();
    });

    expect(screen.getByText("Geplant")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Fertig/i })).not.toBeInTheDocument();
  });
});

type TemplateStub = {
  id: string;
  title: string;
  kind: "basis" | "bonus" | "geld";
  points_value: number;
  bonus_multiplier: number;
  money_value_chf: number;
  icon: string | null;
};

type Call = { method: string; url: string; body: Record<string, unknown> | null };

const BOX: TemplateStub = {
  id: "t1",
  title: "Essensbox ausräumen",
  kind: "basis",
  points_value: 0,
  bonus_multiplier: 1,
  money_value_chf: 0,
  icon: null
};
const BONUS: TemplateStub = {
  id: "t2",
  title: "Rasen mähen",
  kind: "bonus",
  points_value: 10,
  bonus_multiplier: 1.5,
  money_value_chf: 0,
  icon: "🌱"
};
const MONEY: TemplateStub = {
  id: "t3",
  title: "Auto waschen",
  kind: "geld",
  points_value: 0,
  bonus_multiplier: 1,
  money_value_chf: 5,
  icon: "🚗"
};

type Reply = { status: number; body: unknown };

/** Parent session with the given templates. `respond` answers PATCH/DELETE on /tasks/templates/{id};
 *  a successful DELETE also removes the template from later list responses (like the real server). */
function setupParent(
  templates: TemplateStub[],
  respond: (call: Call) => Reply = () => ({ status: 200, body: {} }),
  phases: unknown[] = []
) {
  const payload = btoa(JSON.stringify({ family_id: "fam1", role: "parent_admin", sub: "parent1" }));
  localStorage.setItem("familienplaner_token", `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.${payload}.test`);
  localStorage.setItem("familienplaner_family_id", "fam1");

  const calls: Call[] = [];
  let current = templates;
  const fetchMock = vi.fn((url: string, options?: RequestInit) => {
    const method = options?.method ?? "GET";
    const body = options?.body ? (JSON.parse(String(options.body)) as Record<string, unknown>) : null;
    const call: Call = { method, url, body };
    calls.push(call);
    const reply = (status: number, data: unknown) =>
      Promise.resolve({ ok: status < 400, status, statusText: "x", json: async () => data });

    if (method !== "GET" && url.includes("/tasks/templates/")) {
      const result = respond(call);
      if (method === "DELETE" && result.status < 400) {
        const id = url.split("/tasks/templates/")[1];
        current = current.filter((t) => t.id !== id);
      }
      return reply(result.status, result.body);
    }
    if (method === "POST" && url.endsWith("/tasks/templates")) return reply(201, {});
    if (url.includes("/day-phases")) return reply(200, phases);
    if (url.includes("/tasks/instances")) return reply(200, []);
    if (url.includes("/tasks/templates")) return reply(200, current);
    if (url.includes("/child-accounts")) return reply(200, []);
    return Promise.reject(new Error(`Unknown endpoint: ${url} ${method}`));
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

async function renderTemplates() {
  render(<Tasks />);
  await screen.findAllByRole("button", { name: /Vorlage bearbeiten: / });
}

function clickEdit(title: string) {
  fireEvent.click(screen.getByRole("button", { name: `Vorlage bearbeiten: ${title}` }));
}

describe("Tasks: Vorlagen verwalten", () => {
  it("listet jede Vorlage mit Icon, Titel, Art-Badge sowie Bearbeiten- und Löschen-Button", async () => {
    setupParent([BOX, BONUS, MONEY]);
    await renderTemplates();

    const rows = within(screen.getByRole("list", { name: "Vorlagen" })).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getByText("Essensbox ausräumen")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Basis")).toBeInTheDocument();
    expect(within(rows[1]).getByText("🌱")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Bonus")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Geld")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Vorlage bearbeiten: Rasen mähen" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Vorlage löschen: Rasen mähen" })).toBeInTheDocument();
  });

  it("füllt das Formular beim Bearbeiten vor und schickt nur geänderte Felder per PATCH", async () => {
    const calls = setupParent([BOX, BONUS], () => ({ status: 200, body: { ...BONUS, title: "Garten mähen" } }));
    await renderTemplates();

    clickEdit("Rasen mähen");

    expect(screen.getByLabelText("Titel")).toHaveValue("Rasen mähen");
    expect(screen.getByLabelText("Art")).toHaveValue("bonus");
    expect(screen.getByLabelText("Punkte")).toHaveValue(10);
    expect(screen.getByLabelText("Bonus-Multiplikator")).toHaveValue(1.5);
    expect(screen.getByRole("button", { name: "Vorlage speichern" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Erstellen" })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Titel"), { target: { value: "Garten mähen" } });
    fireEvent.click(screen.getByRole("button", { name: "Vorlage speichern" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    const patch = calls.find((c) => c.method === "PATCH")!;
    expect(patch.url).toContain("/tasks/templates/t2");
    expect(patch.body).toEqual({ title: "Garten mähen" });
    // form leaves edit mode and the list is reloaded
    await screen.findByRole("button", { name: "Erstellen" });
    expect(calls.filter((c) => c.method === "GET" && c.url.endsWith("/tasks/templates")).length).toBeGreaterThan(1);
  });

  it("schickt beim Wechseln des Icons und der Punkte genau diese Felder", async () => {
    const calls = setupParent([BONUS], () => ({ status: 200, body: BONUS }));
    await renderTemplates();

    clickEdit("Rasen mähen");
    fireEvent.change(screen.getByLabelText("Symbol suchen"), { target: { value: "Essensbox" } });
    fireEvent.click(screen.getByRole("button", { name: "Essensbox Znünibox" }));
    fireEvent.change(screen.getByLabelText("Punkte"), { target: { value: "12" } });
    fireEvent.click(screen.getByRole("button", { name: "Vorlage speichern" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ icon: "🍱", points_value: 12 });
  });

  it("setzt beim Wechsel von Bonus auf Basis Punkte und Multiplikator passend zurück", async () => {
    const calls = setupParent([BONUS], () => ({ status: 200, body: BONUS }));
    await renderTemplates();

    clickEdit("Rasen mähen");
    fireEvent.change(screen.getByLabelText("Art"), { target: { value: "basis" } });
    fireEvent.click(screen.getByRole("button", { name: "Vorlage speichern" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({
      kind: "basis",
      points_value: 0,
      bonus_multiplier: 1
    });
  });

  it("sendet nichts, wenn beim Speichern nichts geändert wurde", async () => {
    const calls = setupParent([BOX]);
    await renderTemplates();

    clickEdit("Essensbox ausräumen");
    fireEvent.click(screen.getByRole("button", { name: "Vorlage speichern" }));

    await screen.findByRole("button", { name: "Erstellen" });
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
  });

  it("zeigt die 409-Meldung des Servers an und bleibt im Bearbeiten-Modus", async () => {
    const message = "Art und Punkte lassen sich nicht mehr ändern, weil schon Aufgaben bestätigt wurden.";
    setupParent([BONUS], () => ({ status: 409, body: { detail: message } }));
    await renderTemplates();

    clickEdit("Rasen mähen");
    fireEvent.change(screen.getByLabelText("Punkte"), { target: { value: "99" } });
    fireEvent.click(screen.getByRole("button", { name: "Vorlage speichern" }));

    const alert = await screen.findByText(message);
    expect(alert).toHaveClass("error-banner");
    expect(screen.getByRole("button", { name: "Vorlage speichern" })).toBeInTheDocument();
    expect(screen.getByLabelText("Punkte")).toHaveValue(99);
  });

  it("bricht das Bearbeiten ab und stellt das leere Erstellen-Formular wieder her", async () => {
    const calls = setupParent([BOX]);
    await renderTemplates();

    clickEdit("Essensbox ausräumen");
    fireEvent.change(screen.getByLabelText("Titel"), { target: { value: "Etwas anderes" } });
    fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));

    expect(screen.getByLabelText("Titel")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Erstellen" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Vorlage speichern" })).not.toBeInTheDocument();
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
  });

  it("erstellt weiterhin neue Vorlagen per POST", async () => {
    const calls = setupParent([BOX]);
    await renderTemplates();

    fireEvent.change(screen.getByLabelText("Titel"), { target: { value: "Tisch decken" } });
    fireEvent.click(screen.getByRole("button", { name: "Erstellen" }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.url).toMatch(/\/tasks\/templates$/);
    expect(post.body).toEqual({
      title: "Tisch decken",
      kind: "basis",
      points_value: 0,
      phase_ids: [],
      carry_over: false
    });
  });

  it("löscht in zwei Schritten und meldet 'Vorlage gelöscht'", async () => {
    const calls = setupParent([BOX, BONUS], () => ({ status: 200, body: { result: "deleted" } }));
    await renderTemplates();

    fireEvent.click(screen.getByRole("button", { name: "Vorlage löschen: Essensbox ausräumen" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    const row = screen.getByText("Löschen?").closest("li") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Ja, löschen" }));

    await screen.findByText("Vorlage gelöscht");
    expect(calls.find((c) => c.method === "DELETE")!.url).toContain("/tasks/templates/t1");
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Vorlage bearbeiten: Essensbox ausräumen" })).not.toBeInTheDocument()
    );
    expect(screen.getByRole("button", { name: "Vorlage bearbeiten: Rasen mähen" })).toBeInTheDocument();
    expect(screen.queryByText("Löschen?")).not.toBeInTheDocument();
  });

  it("meldet 'Vorlage ausgeblendet – wird noch verwendet', wenn der Server archiviert", async () => {
    setupParent([BOX], () => ({ status: 200, body: { result: "archived" } }));
    await renderTemplates();

    fireEvent.click(screen.getByRole("button", { name: "Vorlage löschen: Essensbox ausräumen" }));
    fireEvent.click(screen.getByRole("button", { name: "Ja, löschen" }));

    await screen.findByText("Vorlage ausgeblendet – wird noch verwendet");
    await waitFor(() => expect(screen.queryByRole("list", { name: "Vorlagen" })).not.toBeInTheDocument());
  });

  it("lässt alles unverändert, wenn die Löschbestätigung abgebrochen wird", async () => {
    const calls = setupParent([BOX]);
    await renderTemplates();

    fireEvent.click(screen.getByRole("button", { name: "Vorlage löschen: Essensbox ausräumen" }));
    const row = screen.getByText("Löschen?").closest("li") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Abbrechen" }));

    expect(screen.queryByText("Löschen?")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Vorlage löschen: Essensbox ausräumen" })).toBeInTheDocument();
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("zeigt Fehler beim Löschen im Fehlerstil und behält die Vorlage", async () => {
    setupParent([BOX], () => ({ status: 404, body: { detail: "Vorlage nicht gefunden" } }));
    await renderTemplates();

    fireEvent.click(screen.getByRole("button", { name: "Vorlage löschen: Essensbox ausräumen" }));
    fireEvent.click(screen.getByRole("button", { name: "Ja, löschen" }));

    const alert = await screen.findByText("Vorlage nicht gefunden");
    expect(alert).toHaveClass("error-banner");
    expect(screen.getByRole("button", { name: "Vorlage bearbeiten: Essensbox ausräumen" })).toBeInTheDocument();
  });
});
