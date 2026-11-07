import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { todayIsoDate } from "../utils/date";
import Dashboard from "./Dashboard";

const MORGEN = { id: "ph1", name: "Morgen", icon: "🌅", start_time: "06:00:00", end_time: "08:00:00", day_type: "school", sort_order: 0 };
const VORMITTAG = { id: "ph2", name: "Vormittag", icon: "☀️", start_time: "09:00:00", end_time: "12:00:00", day_type: "both", sort_order: 1 };
const ABEND = { id: "ph3", name: "Abend", icon: "🌙", start_time: "19:00:00", end_time: "21:00:00", day_type: "both", sort_order: 2 };
const PHASES = [ABEND, MORGEN, VORMITTAG]; // deliberately unsorted

beforeEach(() => {
  // 10:00 local: Morgen is over, Vormittag is running, Abend has not started. Only Date is faked.
  vi.useFakeTimers({ toFake: ["Date"] });
  const d = new Date();
  vi.setSystemTime(new Date(d.getFullYear(), d.getMonth(), d.getDate(), 10, 0));
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  localStorage.clear();
});

function setToken(claims: Record<string, string>) {
  const payload = btoa(JSON.stringify({ family_id: "fam1", ...claims }));
  localStorage.setItem("familienplaner_token", `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.${payload}.test`);
  localStorage.setItem("familienplaner_family_id", "fam1");
}

function task(id: string, title: string, extra: Record<string, unknown> = {}, assignee = "child1") {
  return {
    id,
    assignee_user_id: assignee,
    due_date: todayIsoDate(),
    status: "open",
    phase_id: null,
    template: { title, kind: "basis", points_value: 0, bonus_multiplier: 1, money_value_chf: 0, icon: "🧹" },
    ...extra,
  };
}

const ANNA = { id: "child1", display_name: "Anna", avatar_key: "🦁", color_index: 0 };

type Calls = { method: string; path: string }[];

function stubBackend(opts: { instances: unknown[]; phases?: unknown[] | "fail"; childAccounts?: unknown[] }): Calls {
  const calls: Calls = [];
  const ok = (body: unknown) => Promise.resolve({ ok: true, status: 200, json: async () => body });
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      const { pathname } = new URL(url);
      calls.push({ method: init?.method ?? "GET", path: pathname });
      if (pathname === "/health") return ok({ status: "ok", hosting_mode: "self_hosted" });
      if (pathname === "/day-phases") {
        return opts.phases === "fail"
          ? Promise.resolve({ ok: false, status: 500, statusText: "x", json: async () => ({ detail: "kaputt" }) })
          : ok(opts.phases ?? PHASES);
      }
      if (pathname === "/families/children") return ok([ANNA]);
      if (pathname === "/families/me") return ok({ name: "Muster" });
      if (pathname === "/families/fam1/child-accounts") return ok(opts.childAccounts ?? [ANNA]);
      if (pathname === "/points/balance") return ok({ balance: 0 });
      if (pathname === "/earnings/balance") return ok({ pending_chf: 0, approved_chf: 0, paid_out_chf: 0 });
      if (pathname === "/tasks/instances" && (init?.method ?? "GET") === "GET") return ok(opts.instances);
      if (/^\/tasks\/instances\/.+\/(complete|reopen)$/.test(pathname)) return ok({});
      if (/^\/(timetables|calendar|weather|rewards|meal-plan|school-breaks)/.test(pathname)) return ok([]);
      return Promise.reject(new Error(`Unknown endpoint: ${url}`));
    })
  );
  return calls;
}

function renderDashboard() {
  return render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>
  );
}

const DAY_TASKS = [
  task("i1", "Abendessen decken", { phase_id: "ph3" }),
  task("i2", "Zähne putzen", { phase_id: "ph1" }),
  task("i3", "Lesen", { phase_id: "ph2" }),
  task("i4", "Zimmer aufräumen"),
];

describe("Dashboard: Kinderansicht nach Tagesphasen", () => {
  it("gruppiert die Kacheln: ohne Phase zuerst ohne Titel, dann nach Beginn mit Icon, Name und 'ab HH:MM'", async () => {
    setToken({ role: "child", sub: "child1" });
    stubBackend({ instances: DAY_TASKS });
    renderDashboard();

    const card = (await screen.findByText("Heute zu tun")).closest(".card") as HTMLElement;
    await within(card).findByRole("region", { name: "Abend" });

    const headings = Array.from(card.querySelectorAll(".phase-heading")).map((h) => h.textContent);
    expect(headings).toEqual(["🌅Morgenab 06:00", "☀️Vormittagab 09:00", "🌙Abendab 19:00"]);

    // phase-less tile comes before the first heading
    const html = card.innerHTML;
    expect(html.indexOf("Zimmer aufräumen")).toBeLessThan(html.indexOf("Morgen"));
    const loose = within(card).getByRole("button", { name: /Zimmer aufräumen/ });
    expect(loose.closest(".phase-group")).toBeNull();
  });

  it("hebt die laufende Phase hervor, frühere bleiben normal, spätere sind gedimmt und gesperrt mit 'ab 19:00'", async () => {
    setToken({ role: "child", sub: "child1" });
    stubBackend({ instances: DAY_TASKS });
    renderDashboard();

    const morgen = await screen.findByRole("region", { name: "Morgen" });
    const vormittag = screen.getByRole("region", { name: "Vormittag" });
    const abend = screen.getByRole("region", { name: "Abend" });

    expect(vormittag).toHaveClass("phase-group--current");
    expect(morgen).toHaveClass("phase-group--past");
    expect(morgen).not.toHaveClass("phase-group--current", "phase-group--upcoming");
    expect(abend).toHaveClass("phase-group--upcoming");

    expect(within(morgen).getByRole("button", { name: /Zähne putzen/ })).toBeEnabled();
    expect(within(vormittag).getByRole("button", { name: /Lesen/ })).toBeEnabled();

    const upcomingTile = within(abend).getByRole("button", { name: /Abendessen decken/ });
    expect(upcomingTile).toBeDisabled();
    expect(upcomingTile).toHaveClass("task-tile--upcoming");
    expect(within(upcomingTile).getByText("ab 19:00")).toBeInTheDocument();
  });

  it("eine gesperrte Kachel löst keine Anfrage aus, eine laufende schon", async () => {
    setToken({ role: "child", sub: "child1" });
    const calls = stubBackend({ instances: DAY_TASKS });
    renderDashboard();

    const abend = await screen.findByRole("region", { name: "Abend" });
    fireEvent.click(within(abend).getByRole("button", { name: /Abendessen decken/ }));
    expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/complete"))).toBe(false);

    fireEvent.click(within(screen.getByRole("region", { name: "Vormittag" })).getByRole("button", { name: /Lesen/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/tasks/instances/i3/complete")).toBe(true));
  });

  it("behält die stabile Reihenfolge (Titel) innerhalb einer Phase", async () => {
    setToken({ role: "child", sub: "child1" });
    stubBackend({
      instances: [
        task("a", "Wäsche", { phase_id: "ph2" }),
        task("b", "Blumen giessen", { phase_id: "ph2" }),
        task("c", "Tisch decken", { phase_id: "ph2" }),
      ],
    });
    renderDashboard();

    const region = await screen.findByRole("region", { name: "Vormittag" });
    const titles = within(region)
      .getAllByRole("button")
      .map((b) => b.querySelector(".task-tile-title")?.textContent);
    expect(titles).toEqual(["Blumen giessen", "Tisch decken", "Wäsche"]);
  });

  it("ohne Phasen bleibt es ein einfaches Kachelraster ohne Überschriften", async () => {
    setToken({ role: "child", sub: "child1" });
    stubBackend({ instances: DAY_TASKS, phases: [] });
    renderDashboard();

    await screen.findByRole("button", { name: /Zimmer aufräumen/ });
    expect(screen.getAllByRole("button", { name: /offen, antippen/ })).toHaveLength(4);
    expect(document.querySelector(".phase-heading")).toBeNull();
  });

  it("ein fehlschlagendes /day-phases zählt als keine Phasen und bricht die Seite nicht", async () => {
    setToken({ role: "child", sub: "child1" });
    stubBackend({ instances: DAY_TASKS, phases: "fail" });
    renderDashboard();

    expect(await screen.findByRole("button", { name: /Abendessen decken/ })).toBeEnabled();
    expect(document.querySelector(".phase-heading")).toBeNull();
  });

  it("Aufgaben einer unbekannten Phase werden wie ohne Phase gezeigt", async () => {
    setToken({ role: "child", sub: "child1" });
    stubBackend({ instances: [task("x", "Rätsel", { phase_id: "weg" })] });
    renderDashboard();

    expect(await screen.findByRole("button", { name: /Rätsel/ })).toBeEnabled();
  });
});

describe("Dashboard: verfallene Aufgaben", () => {
  it("erscheinen weder als Kachel noch im Fortschritt der Kinderansicht", async () => {
    setToken({ role: "child", sub: "child1" });
    stubBackend({
      instances: [
        task("i1", "Offen heute"),
        task("i2", "Erledigt heute", { status: "completed" }),
        task("i3", "Verpasst", { status: "expired" }),
        task("i4", "Verpasst 2", { status: "expired" }),
      ],
      phases: [],
    });
    renderDashboard();

    await screen.findByRole("button", { name: /Offen heute/ });
    expect(screen.queryByText("Verpasst")).toBeNull();
    expect(screen.queryByText("Verpasst 2")).toBeNull();
    expect(await screen.findByText("1/2 erledigt")).toBeInTheDocument();
  });

  it("zählen auf dem Wand-Dashboard nicht in den Kinder-Kacheln", async () => {
    setToken({ role: "dashboard", sub: "wall" });
    stubBackend({
      instances: [
        task("i1", "Offen heute"),
        task("i2", "Verpasst", { status: "expired" }),
        task("i3", "Verpasst 2", { status: "expired" }),
      ],
      phases: [],
    });
    renderDashboard();

    await screen.findByRole("button", { name: /Offen heute/ });
    expect(screen.queryByText("Verpasst")).toBeNull();
    // no progress counter at all: nothing done, one real task -> "0/1"
    const tile = screen.getByText("Anna", { selector: ".family-tile-name" }).closest(".family-tile") as HTMLElement;
    expect(within(tile).getByLabelText("0 von 1 erledigt")).toBeInTheDocument();
  });

  it("eine Woche nur mit verfallenen Aufgaben zeigt 'Nichts geplant' und 'Heute frei'", async () => {
    setToken({ role: "child", sub: "child1" });
    stubBackend({ instances: [task("i1", "Verpasst", { status: "expired" })], phases: [] });
    renderDashboard();

    expect(await screen.findByText("Nichts geplant")).toBeInTheDocument();
    expect(screen.getAllByText(/Heute frei/).length).toBeGreaterThan(0);
  });
});

describe("Dashboard: Wand-Dashboard nach Tagesphasen", () => {
  it("gruppiert die Kacheln jedes Kindes nach Phase, mit Hervorhebung und gesperrter späterer Phase", async () => {
    setToken({ role: "dashboard", sub: "wall" });
    stubBackend({ instances: DAY_TASKS });
    renderDashboard();

    const tile = (await screen.findByText("Anna", { selector: ".family-tile-name" })).closest(".family-tile") as HTMLElement;
    const vormittag = await within(tile).findByRole("region", { name: "Vormittag" });
    const abend = within(tile).getByRole("region", { name: "Abend" });

    expect(vormittag).toHaveClass("phase-group--current");
    expect(abend).toHaveClass("phase-group--upcoming");
    expect(within(abend).getByRole("button", { name: /Abendessen decken/ })).toBeDisabled();
    expect(within(abend).getByText("ab 19:00", { selector: ".task-tile-from" })).toBeInTheDocument();
    expect(within(tile).getByRole("button", { name: /Zimmer aufräumen/ }).closest(".phase-group")).toBeNull();
  });
});
