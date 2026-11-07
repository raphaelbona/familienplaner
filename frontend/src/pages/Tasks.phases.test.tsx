import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Tasks from "./Tasks";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  localStorage.clear();
});

const MORGEN = {
  id: "ph1",
  name: "Morgen",
  icon: "🌅",
  start_time: "06:00:00",
  end_time: "08:00:00",
  day_type: "school",
  sort_order: 0,
};
const AUSSCHLAFEN = {
  id: "ph2",
  name: "Ausschlafen",
  icon: "🛏️",
  start_time: "08:00:00",
  end_time: "10:00:00",
  day_type: "free",
  sort_order: 1,
};
const ABEND = {
  id: "ph3",
  name: "Abend",
  icon: "🌙",
  start_time: "19:00:00",
  end_time: "21:00:00",
  day_type: "both",
  sort_order: 2,
};
const PHASES = [MORGEN, AUSSCHLAFEN, ABEND];

type Template = {
  id: string;
  title: string;
  kind: "basis" | "bonus" | "geld";
  points_value: number;
  bonus_multiplier: number;
  money_value_chf: number;
  icon: string | null;
  phase_ids?: string[];
  carry_over?: boolean;
};

const BOX: Template = {
  id: "t1",
  title: "Essensbox ausräumen",
  kind: "basis",
  points_value: 0,
  bonus_multiplier: 1,
  money_value_chf: 0,
  icon: null,
};
const BONUS: Template = {
  id: "t2",
  title: "Rasen mähen",
  kind: "bonus",
  points_value: 10,
  bonus_multiplier: 1.5,
  money_value_chf: 0,
  icon: "🌱",
};

type Call = { method: string; url: string; body: Record<string, unknown> | null };

function reply(status: number, data: unknown) {
  return Promise.resolve({ ok: status < 400, status, statusText: "x", json: async () => data });
}

function setToken(claims: Record<string, string>) {
  const payload = btoa(JSON.stringify({ family_id: "fam1", ...claims }));
  localStorage.setItem("familienplaner_token", `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.${payload}.test`);
  localStorage.setItem("familienplaner_family_id", "fam1");
}

function setupParent(templates: Template[], phases: unknown[] = PHASES) {
  setToken({ role: "parent_admin", sub: "parent1" });
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, options?: RequestInit) => {
      const method = options?.method ?? "GET";
      const body = options?.body ? (JSON.parse(String(options.body)) as Record<string, unknown>) : null;
      calls.push({ method, url, body });
      if (method === "PATCH" && url.includes("/tasks/templates/")) return reply(200, BOX);
      if (method === "POST" && url.endsWith("/tasks/templates")) return reply(201, {});
      if (url.includes("/day-phases")) return reply(200, phases);
      if (url.includes("/tasks/instances")) return reply(200, []);
      if (url.includes("/tasks/templates")) return reply(200, templates);
      if (url.includes("/child-accounts")) return reply(200, []);
      return Promise.reject(new Error(`Unknown endpoint: ${url} ${method}`));
    })
  );
  return calls;
}

async function renderTemplates() {
  render(<Tasks />);
  await screen.findAllByRole("button", { name: /Vorlage bearbeiten: / });
}

describe("Tasks: Vorlagen mit Tagesphasen", () => {
  it("zeigt die Phasen gruppiert nach Schultage und Freie Tage; 'Beide' erscheint in beiden, ist aber eine Auswahl", async () => {
    setupParent([BOX]);
    await renderTemplates();

    const school = await screen.findByRole("group", { name: "Schultage" });
    const free = screen.getByRole("group", { name: "Freie Tage" });
    expect(within(school).getAllByRole("button").map((b) => b.textContent)).toEqual(["🌅Morgen", "🌙Abend"]);
    expect(within(free).getAllByRole("button").map((b) => b.textContent)).toEqual(["🛏️Ausschlafen", "🌙Abend"]);

    fireEvent.click(within(school).getByRole("button", { name: /Abend/ }));
    expect(within(school).getByRole("button", { name: /Abend/ })).toHaveAttribute("aria-pressed", "true");
    expect(within(free).getByRole("button", { name: /Abend/ })).toHaveAttribute("aria-pressed", "true");
    expect(within(free).getByRole("button", { name: /Ausschlafen/ })).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(within(free).getByRole("button", { name: /Abend/ }));
    expect(within(school).getByRole("button", { name: /Abend/ })).toHaveAttribute("aria-pressed", "false");
  });

  it("blendet die Phasenwahl ohne Phasen aus", async () => {
    setupParent([BOX], []);
    await renderTemplates();
    expect(screen.queryByRole("group", { name: "Schultage" })).toBeNull();
  });

  it("'Verpasste Aufgaben aufsummieren' ist standardmässig aus und hat einen kurzen Hinweis", async () => {
    setupParent([BOX]);
    await renderTemplates();

    expect(screen.getByLabelText("Verpasste Aufgaben aufsummieren")).not.toBeChecked();
    expect(screen.getByText("Aus: Verpasstes verfällt, sobald die nächste fällig ist.")).toBeInTheDocument();
  });

  it("sendet gewählte Phasen und carry_over beim Erstellen", async () => {
    const calls = setupParent([BOX]);
    await renderTemplates();

    fireEvent.change(screen.getByLabelText("Titel"), { target: { value: "Zähne putzen" } });
    fireEvent.click(within(await screen.findByRole("group", { name: "Schultage" })).getByRole("button", { name: /Morgen/ }));
    fireEvent.click(within(screen.getByRole("group", { name: "Freie Tage" })).getByRole("button", { name: /Abend/ }));
    fireEvent.click(screen.getByLabelText("Verpasste Aufgaben aufsummieren"));
    fireEvent.click(screen.getByRole("button", { name: "Erstellen" }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({
      title: "Zähne putzen",
      kind: "basis",
      points_value: 0,
      phase_ids: ["ph1", "ph3"],
      carry_over: true,
    });
  });

  it("zeigt beim Bearbeiten die bisherige Auswahl und sendet nur die geänderten Felder", async () => {
    const calls = setupParent([{ ...BOX, phase_ids: ["ph1"], carry_over: false }]);
    await renderTemplates();

    fireEvent.click(screen.getByRole("button", { name: "Vorlage bearbeiten: Essensbox ausräumen" }));
    const school = screen.getByRole("group", { name: "Schultage" });
    expect(within(school).getByRole("button", { name: /Morgen/ })).toHaveAttribute("aria-pressed", "true");
    expect(within(school).getByRole("button", { name: /Abend/ })).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(within(school).getByRole("button", { name: /Abend/ }));
    fireEvent.click(screen.getByRole("button", { name: "Vorlage speichern" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ phase_ids: ["ph1", "ph3"] });
  });

  it("sendet beim Bearbeiten carry_over, wenn es umgeschaltet wurde", async () => {
    const calls = setupParent([BOX]);
    await renderTemplates();

    fireEvent.click(screen.getByRole("button", { name: "Vorlage bearbeiten: Essensbox ausräumen" }));
    expect(screen.getByLabelText("Verpasste Aufgaben aufsummieren")).not.toBeChecked();
    fireEvent.click(screen.getByLabelText("Verpasste Aufgaben aufsummieren"));
    fireEvent.click(screen.getByRole("button", { name: "Vorlage speichern" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ carry_over: true });
  });

  it("zeigt die Phasen-Icons und ein Aufsummieren-Zeichen in der Vorlagenzeile", async () => {
    setupParent([{ ...BOX, phase_ids: ["ph1", "ph3"], carry_over: true }, BONUS]);
    await renderTemplates();

    const rows = within(screen.getByRole("list", { name: "Vorlagen" })).getAllByRole("listitem");
    await waitFor(() => expect(within(rows[0]).getByRole("img", { name: "Morgen" })).toHaveTextContent("🌅"));
    expect(within(rows[0]).getByRole("img", { name: "Abend" })).toHaveTextContent("🌙");
    expect(within(rows[0]).queryByRole("img", { name: "Ausschlafen" })).toBeNull();
    expect(within(rows[0]).getByRole("img", { name: "Verpasstes wird aufsummiert" })).toBeInTheDocument();
    expect(within(rows[1]).queryByRole("img")).toBeNull();
  });

  it("enthält die Tagesphasen-Karte für Eltern", async () => {
    setupParent([BOX]);
    await renderTemplates();
    expect(await screen.findByRole("list", { name: "🏫 Schultage" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Tagesphasen" })).toBeInTheDocument();
  });
});

describe("Tasks: Aufgaben nach Tagesphase", () => {
  function instance(id: string, title: string, extra: Record<string, unknown> = {}) {
    const d = new Date();
    const today = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    return {
      id,
      assignee_user_id: "c1",
      due_date: today,
      status: "open",
      phase_id: null,
      template: { title, kind: "basis", points_value: 0, bonus_multiplier: 1, money_value_chf: 0, icon: null },
      ...extra,
    };
  }

  function setupChild(instances: unknown[], phases: unknown[] | "fail") {
    setToken({ role: "child", sub: "c1" });
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (url.includes("/day-phases")) return phases === "fail" ? reply(500, { detail: "kaputt" }) : reply(200, phases);
        if (url.includes("/tasks/instances")) return reply(200, instances);
        return Promise.reject(new Error(`Unknown endpoint: ${url}`));
      })
    );
  }

  /** Fixes the local clock at 10:00 today (only Date, so timers and async keep working). */
  function atTenOClock() {
    vi.useFakeTimers({ toFake: ["Date"] });
    const d = new Date();
    vi.setSystemTime(new Date(d.getFullYear(), d.getMonth(), d.getDate(), 10, 0));
  }

  it("gruppiert die heutigen Karten nach Phase: ohne Phase zuerst, dann nach Beginn; spätere Phase gesperrt mit 'ab 19:00'", async () => {
    atTenOClock();
    setupChild(
      [
        instance("i1", "Abendessen decken", { phase_id: "ph3" }),
        instance("i2", "Zähne putzen", { phase_id: "ph1" }),
        instance("i3", "Zimmer aufräumen"),
      ],
      PHASES
    );
    render(<Tasks />);

    const morgen = await screen.findByRole("region", { name: "Morgen" });
    const abend = screen.getByRole("region", { name: "Abend" });
    expect(within(morgen).getByText("Zähne putzen")).toBeInTheDocument();
    expect(within(morgen).getByText("ab 06:00")).toBeInTheDocument();
    expect(morgen).toHaveClass("phase-group--past");
    expect(abend).toHaveClass("phase-group--upcoming");
    expect(within(abend).getByRole("button", { name: /ab 19:00/ })).toBeDisabled();
    expect(within(morgen).getByRole("button", { name: /Fertig!/ })).toBeEnabled();

    const html = document.body.innerHTML;
    expect(html.indexOf("Zimmer aufräumen")).toBeLessThan(html.indexOf("Zähne putzen"));
    expect(html.indexOf("Zähne putzen")).toBeLessThan(html.indexOf("Abendessen decken"));
  });

  it("hebt die aktuelle Phase hervor", async () => {
    atTenOClock();
    setupChild([instance("i1", "Tee trinken", { phase_id: "ph4" })], [
      ...PHASES,
      { ...MORGEN, id: "ph4", name: "Vormittag", start_time: "09:00:00", end_time: "12:00:00" },
    ]);
    render(<Tasks />);

    const region = await screen.findByRole("region", { name: "Vormittag" });
    expect(region).toHaveClass("phase-group--current");
  });

  it("zeigt ein Phasen-Chip neben dem Titel", async () => {
    atTenOClock();
    setupChild([instance("i1", "Lesen", { phase_id: "ph2" })], PHASES);
    render(<Tasks />);

    const card = (await screen.findByText("Lesen")).closest(".task-board-card") as HTMLElement;
    await waitFor(() => expect(card.querySelector(".phase-chip")).not.toBeNull());
    expect(card.querySelector(".phase-chip")).toHaveTextContent("🛏️Ausschlafen");
  });

  it("versteckt verfallene Aufgaben und zählt sie nicht", async () => {
    atTenOClock();
    setupChild([instance("i1", "Offen heute"), instance("i2", "Verpasst gestern", { status: "expired" })], []);
    render(<Tasks />);

    await screen.findByText("Offen heute");
    expect(screen.queryByText("Verpasst gestern")).toBeNull();
    expect(screen.getByRole("tab", { name: /Heute/ })).toHaveTextContent("1");
  });

  it("funktioniert weiter, wenn /day-phases fehlschlägt (keine Phasen)", async () => {
    atTenOClock();
    setupChild([instance("i1", "Lesen", { phase_id: "ph2" })], "fail");
    render(<Tasks />);

    expect(await screen.findByText("Lesen")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Fertig!/ })).toBeEnabled();
    expect(screen.queryByRole("region")).toBeNull();
  });

  it("zeigt Kindern heute alle Karten, wenn es Phasen gibt (kein 5er-Limit)", async () => {
    atTenOClock();
    setupChild(
      Array.from({ length: 7 }, (_, i) => instance(`i${i}`, `Aufgabe ${i}`, { phase_id: "ph1" })),
      PHASES
    );
    render(<Tasks />);

    await screen.findByText("Aufgabe 0");
    await waitFor(() => expect(screen.getAllByText(/^Aufgabe \d$/)).toHaveLength(7));
  });
});
