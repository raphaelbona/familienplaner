import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DayPhase } from "../api/dayPhases";
import DayPhasesCard from "./DayPhasesCard";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

function phase(extra: Partial<DayPhase> & { id: string }): DayPhase {
  return {
    name: "Morgen",
    icon: "🌅",
    start_time: "06:00:00",
    end_time: "08:00:00",
    day_type: "school",
    sort_order: 0,
    ...extra,
  };
}

const DEFAULTS: DayPhase[] = [
  phase({ id: "d1", name: "Morgen", day_type: "school" }),
  phase({ id: "d2", name: "Ausschlafen", icon: "🛏️", start_time: "08:00:00", end_time: "10:00:00", day_type: "free" }),
  phase({ id: "d3", name: "Abend", icon: "🌙", start_time: "19:00:00", end_time: "21:00:00", day_type: "both" }),
];

type Call = { method: string; path: string; body?: Record<string, unknown> };

function stubApi(
  initial: DayPhase[],
  opts: { seed?: "ok" | "conflict"; failList?: boolean; failPost?: { status: number; detail: string } } = {}
) {
  let rows = [...initial];
  const calls: Call[] = [];
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    const path = String(url).replace(/^https?:\/\/[^/]+/, "");
    const method = init?.method ?? "GET";
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined;
    calls.push({ method, path, body });
    const respond = (data: unknown, status = 200) =>
      Promise.resolve({ ok: status < 400, status, statusText: "", headers: new Headers(), json: async () => data });

    if (path === "/day-phases" && method === "GET") {
      return opts.failList ? respond({ detail: "Server kaputt" }, 500) : respond(rows);
    }
    if (path === "/day-phases/defaults" && method === "POST") {
      if (opts.seed === "conflict") {
        // Another device set them up in the meantime.
        rows = DEFAULTS;
        return respond({ detail: "Es gibt schon Tagesphasen." }, 409);
      }
      rows = DEFAULTS;
      return respond(rows, 201);
    }
    if (path === "/day-phases" && method === "POST") {
      if (opts.failPost) return respond({ detail: opts.failPost.detail }, opts.failPost.status);
      const created = phase({ id: `new-${rows.length}`, ...(body as Partial<DayPhase>) });
      rows = [...rows, created];
      return respond(created, 201);
    }
    const match = /^\/day-phases\/(.+)$/.exec(path);
    if (match && method === "PATCH") {
      rows = rows.map((r) => (r.id === match[1] ? { ...r, ...(body as Partial<DayPhase>) } : r));
      return respond(rows.find((r) => r.id === match[1]));
    }
    if (match && method === "DELETE") {
      rows = rows.filter((r) => r.id !== match[1]);
      return Promise.resolve({ ok: true, status: 204, statusText: "", headers: new Headers(), json: async () => undefined });
    }
    return Promise.reject(new Error(`Unknown endpoint: ${method} ${path}`));
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls };
}

describe("DayPhasesCard: Liste", () => {
  it("gruppiert nach Schultage, Freie Tage und Beide mit Icon, Name und Zeitraum", async () => {
    stubApi(DEFAULTS);
    render(<DayPhasesCard />);

    const school = await screen.findByRole("list", { name: "🏫 Schultage" });
    const free = screen.getByRole("list", { name: "🏖️ Freie Tage" });
    const both = screen.getByRole("list", { name: "🔁 Beide" });

    const morgen = within(school).getByRole("listitem");
    expect(within(morgen).getByText("Morgen")).toBeInTheDocument();
    expect(within(morgen).getByText("06:00–08:00")).toBeInTheDocument();
    expect(within(morgen).getByText("🌅")).toBeInTheDocument();
    expect(within(free).getByText("Ausschlafen")).toBeInTheDocument();
    expect(within(both).getByText("19:00–21:00")).toBeInTheDocument();
  });

  it("sortiert innerhalb einer Gruppe nach Beginn", async () => {
    stubApi([
      phase({ id: "b", name: "Später", start_time: "14:00:00", end_time: "16:00:00" }),
      phase({ id: "a", name: "Früher", start_time: "07:00:00", end_time: "09:00:00" }),
    ]);
    render(<DayPhasesCard />);

    const rows = within(await screen.findByRole("list", { name: "🏫 Schultage" })).getAllByRole("listitem");
    expect(rows.map((r) => within(r).getByText(/Früher|Später/).textContent)).toEqual(["Früher", "Später"]);
  });

  it("lässt Gruppen ohne Phasen weg", async () => {
    stubApi([phase({ id: "a" })]);
    render(<DayPhasesCard />);
    await screen.findByRole("list", { name: "🏫 Schultage" });
    expect(screen.queryByRole("list", { name: "🏖️ Freie Tage" })).toBeNull();
    expect(screen.queryByRole("list", { name: "🔁 Beide" })).toBeNull();
  });

  it("meldet die geladenen Phasen an die Seite", async () => {
    stubApi(DEFAULTS);
    const onChange = vi.fn();
    render(<DayPhasesCard onChange={onChange} />);
    await screen.findByRole("list", { name: "🏫 Schultage" });
    expect(onChange).toHaveBeenCalledWith(DEFAULTS);
  });

  it("zeigt einen Ladefehler im Fehlerstil", async () => {
    stubApi([], { failList: true });
    render(<DayPhasesCard />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Server kaputt");
  });
});

describe("DayPhasesCard: leer", () => {
  it("bietet 'Standard-Phasen einrichten' und danach die Liste", async () => {
    const { calls } = stubApi([]);
    render(<DayPhasesCard />);

    fireEvent.click(await screen.findByRole("button", { name: /Standard-Phasen einrichten/ }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/day-phases/defaults")).toBe(true));
    expect(await screen.findByRole("list", { name: "🏫 Schultage" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Standard-Phasen einrichten/ })).toBeNull();
  });

  it("bei 409 wird die Liste still neu geladen, ohne Fehlermeldung", async () => {
    const { calls } = stubApi([], { seed: "conflict" });
    render(<DayPhasesCard />);

    fireEvent.click(await screen.findByRole("button", { name: /Standard-Phasen einrichten/ }));

    expect(await screen.findByRole("list", { name: "🏫 Schultage" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(calls.filter((c) => c.method === "GET")).toHaveLength(2);
  });

  it("bietet auch das Hinzufügen einer eigenen Phase an", async () => {
    stubApi([]);
    render(<DayPhasesCard />);
    expect(await screen.findByRole("button", { name: /Phase hinzufügen/ })).toBeInTheDocument();
  });
});

describe("DayPhasesCard: Hinzufügen", () => {
  it("legt eine Phase mit Name, Icon, Zeiten und 'Gilt an' an", async () => {
    const { calls } = stubApi([phase({ id: "x" })]);
    render(<DayPhasesCard />);
    await screen.findByRole("list", { name: "🏫 Schultage" });

    fireEvent.click(screen.getByRole("button", { name: /Phase hinzufügen/ }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Mittag" } });
    fireEvent.change(screen.getByLabelText("Icon"), { target: { value: "🍽️" } });
    fireEvent.change(screen.getByLabelText("Von"), { target: { value: "11:30" } });
    fireEvent.change(screen.getByLabelText("Bis"), { target: { value: "13:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Freie Tage" }));
    expect(screen.getByRole("button", { name: "Freie Tage" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/day-phases")).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      name: "Mittag",
      icon: "🍽️",
      start_time: "11:30:00",
      end_time: "13:00:00",
      day_type: "free",
    });
    expect(await screen.findByRole("list", { name: "🏖️ Freie Tage" })).toBeInTheDocument();
    expect(screen.queryByRole("form")).toBeNull();
  });

  it("nimmt ein Icon-Vorschlag per Tipp und sendet kein Icon, wenn keins gewählt ist", async () => {
    const { calls } = stubApi([]);
    render(<DayPhasesCard />);
    await screen.findByRole("button", { name: /Standard-Phasen einrichten/ });

    fireEvent.click(screen.getByRole("button", { name: /Phase hinzufügen/ }));
    fireEvent.click(screen.getByRole("button", { name: "Icon 🌙" }));
    expect(screen.getByLabelText("Icon")).toHaveValue("🌙");
    fireEvent.change(screen.getByLabelText("Icon"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Nacht" } });
    fireEvent.change(screen.getByLabelText("Von"), { target: { value: "21:00" } });
    fireEvent.change(screen.getByLabelText("Bis"), { target: { value: "22:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    // "Gilt an" defaults to both
    expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ icon: null, day_type: "both" });
  });

  it("prüft Name, Zeiten und Reihenfolge, bevor etwas gesendet wird", async () => {
    const { calls } = stubApi([]);
    render(<DayPhasesCard />);
    await screen.findByRole("button", { name: /Standard-Phasen einrichten/ });
    fireEvent.click(screen.getByRole("button", { name: /Phase hinzufügen/ }));

    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Bitte einen Namen eingeben.");

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "X" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Bitte Von und Bis wählen.");

    fireEvent.change(screen.getByLabelText("Von"), { target: { value: "10:00" } });
    fireEvent.change(screen.getByLabelText("Bis"), { target: { value: "09:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Das Ende muss nach dem Beginn liegen.");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("zeigt die Fehlermeldung des Servers im Formular und lässt es offen", async () => {
    stubApi([], { failPost: { status: 422, detail: "Name schon vergeben" } });
    render(<DayPhasesCard />);
    await screen.findByRole("button", { name: /Standard-Phasen einrichten/ });
    fireEvent.click(screen.getByRole("button", { name: /Phase hinzufügen/ }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Morgen" } });
    fireEvent.change(screen.getByLabelText("Von"), { target: { value: "06:00" } });
    fireEvent.change(screen.getByLabelText("Bis"), { target: { value: "08:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    expect(await screen.findByText("Name schon vergeben")).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("Morgen");
  });
});

describe("DayPhasesCard: Bearbeiten und Löschen", () => {
  it("bearbeitet eine Phase mit PATCH und füllt das Formular vor", async () => {
    const { calls } = stubApi([phase({ id: "p1" })]);
    render(<DayPhasesCard />);

    fireEvent.click(await screen.findByRole("button", { name: "Phase bearbeiten: Morgen" }));
    expect(screen.getByLabelText("Name")).toHaveValue("Morgen");
    expect(screen.getByLabelText("Von")).toHaveValue("06:00");
    expect(screen.getByLabelText("Bis")).toHaveValue("08:00");
    expect(screen.getByRole("button", { name: "Schultage" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.change(screen.getByLabelText("Bis"), { target: { value: "08:30" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")).toMatchObject({
      path: "/day-phases/p1",
      body: { name: "Morgen", start_time: "06:00:00", end_time: "08:30:00", day_type: "school" },
    });
    expect(await screen.findByText("06:00–08:30")).toBeInTheDocument();
  });

  it("Löschen braucht zwei Schritte und kann abgebrochen werden", async () => {
    const { calls } = stubApi([phase({ id: "p1" })]);
    render(<DayPhasesCard />);

    fireEvent.click(await screen.findByRole("button", { name: "Phase löschen: Morgen" }));
    expect(screen.getByText("Löschen?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
    expect(screen.queryByText("Löschen?")).toBeNull();
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Phase löschen: Morgen" }));
    fireEvent.click(screen.getByRole("button", { name: "Ja, löschen" }));

    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    expect(calls.find((c) => c.method === "DELETE")?.path).toBe("/day-phases/p1");
    expect(await screen.findByText("Noch keine Tagesphasen.")).toBeInTheDocument();
  });

  it("die Aktionen sind icon-btn (44 px) mit blauem Stift und grauem Papierkorb", async () => {
    stubApi([phase({ id: "p1" })]);
    render(<DayPhasesCard />);
    const edit = await screen.findByRole("button", { name: "Phase bearbeiten: Morgen" });
    const del = screen.getByRole("button", { name: "Phase löschen: Morgen" });
    expect(edit).toHaveClass("icon-btn");
    expect(edit).not.toHaveClass("secondary");
    expect(del).toHaveClass("icon-btn", "secondary");
  });
});
