import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SchoolBreak } from "../api/schoolBreaks";
import { localIsoDateOffset } from "../utils/date";
import Dashboard from "./Dashboard";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

type Kid = { id: string; display_name: string; avatar_key: string; color_index: number };

const ANNA: Kid = { id: "child1", display_name: "Anna", avatar_key: "🦁", color_index: 0 };
const NOAH: Kid = { id: "child2", display_name: "Noah", avatar_key: "🐯", color_index: 1 };

function setToken(claims: Record<string, string>) {
  const payload = btoa(JSON.stringify({ family_id: "fam1", ...claims }));
  localStorage.setItem("familienplaner_token", `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.${payload}.test`);
  localStorage.setItem("familienplaner_family_id", "fam1");
}

function schoolBreak(extra: Partial<SchoolBreak> = {}): SchoolBreak {
  return {
    id: "b1",
    title: "Herbstferien",
    kind: "ferien",
    start_date: localIsoDateOffset(0),
    end_date: localIsoDateOffset(1),
    child_user_ids: [],
    source: "manual",
    ...extra,
  };
}

/** Lessons on every weekday, so a lesson block is drawn on any day the tests run on. */
const EVERY_DAY = [0, 1, 2, 3, 4, 5, 6].map((day) => ({
  id: `tt-${day}`,
  day_of_week: day,
  start_time: "08:00",
  end_time: "09:00",
  subject: "Deutsch",
  room: null,
}));

function stubBackend(opts: { children: Kid[]; childAccounts: Kid[]; breaks: SchoolBreak[]; breaksFail?: boolean }) {
  const ok = (body: unknown) => Promise.resolve({ ok: true, status: 200, json: async () => body });
  const fetchMock = vi.fn((url: string) => {
    const { pathname } = new URL(url);
    if (pathname === "/health") return ok({ status: "ok", hosting_mode: "self_hosted" });
    if (pathname === "/school-breaks") {
      return opts.breaksFail ? Promise.reject(new Error("kaputt")) : ok(opts.breaks);
    }
    if (pathname === "/families/children") return ok(opts.children);
    if (pathname === "/families/me") return ok({ name: "Muster" });
    if (pathname === "/families/fam1/child-accounts") return ok(opts.childAccounts);
    if (pathname === "/points/balance") return ok({ balance: 0 });
    if (pathname === "/earnings/balance") return ok({ pending_chf: 0, approved_chf: 0, paid_out_chf: 0 });
    if (pathname.startsWith("/timetables/")) return ok(EVERY_DAY);
    if (/^\/(tasks|calendar|weather|rewards|meal-plan)/.test(pathname)) return ok([]);
    return Promise.reject(new Error(`Unknown endpoint: ${url}`));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderDashboard() {
  return render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>
  );
}

async function cell(table: HTMLElement, name: RegExp) {
  return within(table).findByRole("img", { name });
}

describe("Dashboard: Ferien und freie Tage", () => {
  it("Wand-Dashboard: ein Kind in den Ferien bekommt die Ferienzelle, das andere seinen Stundenplan", async () => {
    setToken({ role: "dashboard", sub: "wall" });
    stubBackend({ children: [ANNA, NOAH], childAccounts: [], breaks: [schoolBreak({ child_user_ids: [ANNA.id] })] });

    renderDashboard();

    const table = await screen.findByRole("table", { name: "Heute und Morgen" });
    const annaToday = await cell(table, /^Anna, Heute: Ferien: Herbstferien/);
    expect(annaToday).toHaveClass("daybar--holiday");
    expect(within(annaToday).getByText("Herbstferien")).toBeInTheDocument();
    expect(annaToday.querySelector('[data-icon="beach"]')).not.toBeNull();
    expect(annaToday.closest(".day-cell")!.querySelector(".daybar-block")).toBeNull(); // no school block

    const noahToday = await within(table).findByRole("img", { name: /^Noah, Heute: Schule/ });
    expect(noahToday.closest(".day-cell")!.querySelector(".daybar-block")).not.toBeNull();
    expect(within(table).queryByRole("img", { name: /^Noah, .*Ferien/ })).toBeNull();
  });

  it("eine Ferien-Eintragung ohne Kinder gilt für alle Kinder und die Familien-Zeile bleibt unberührt", async () => {
    setToken({ role: "dashboard", sub: "wall" });
    stubBackend({ children: [ANNA, NOAH], childAccounts: [], breaks: [schoolBreak()] });

    renderDashboard();

    const table = await screen.findByRole("table", { name: "Heute und Morgen" });
    expect(await cell(table, /^Anna, Heute: Ferien/)).toBeInTheDocument();
    expect(await cell(table, /^Noah, Heute: Ferien/)).toBeInTheDocument();
    expect(await cell(table, /^Familie, Heute: nichts geplant/)).toBeInTheDocument();
  });

  it("holt die Ferien einmal pro Laden für heute bis heute+60 Tage", async () => {
    setToken({ role: "dashboard", sub: "wall" });
    const fetchMock = stubBackend({ children: [ANNA], childAccounts: [], breaks: [] });

    renderDashboard();
    await screen.findByRole("table", { name: "Heute und Morgen" });

    const calls = fetchMock.mock.calls.map((c) => String(c[0])).filter((u) => u.includes("/school-breaks"));
    expect(calls).toHaveLength(1);
    const params = new URL(calls[0]).searchParams;
    expect(params.get("from")).toBe(localIsoDateOffset(0));
    expect(params.get("to")).toBe(localIsoDateOffset(60));
  });

  it("Kind-Ansicht: das eigene Dashboard zeigt die Ferienzelle (Server liefert nur []/eigene Id)", async () => {
    setToken({ role: "child", sub: "child1" });
    stubBackend({ children: [], childAccounts: [ANNA], breaks: [schoolBreak({ child_user_ids: [ANNA.id] })] });

    renderDashboard();

    const heading = await screen.findByText("Heute und Morgen");
    const card = heading.closest(".card") as HTMLElement;
    expect(await within(card).findByRole("img", { name: /^Anna, Heute: Ferien: Herbstferien/ })).toBeInTheDocument();
    expect(card.querySelector(".daybar-block")).toBeNull();
  });

  it("Kind-Ansicht: eine Eintragung für alle (leere Liste) gilt auch für das Kind", async () => {
    setToken({ role: "child", sub: "child2" });
    stubBackend({ children: [], childAccounts: [NOAH], breaks: [schoolBreak()] });

    renderDashboard();

    const card = (await screen.findByText("Heute und Morgen")).closest(".card") as HTMLElement;
    expect(await within(card).findByRole("img", { name: /^Noah, Heute: Ferien/ })).toBeInTheDocument();
  });

  it("ohne Ferien oder wenn der Abruf scheitert, bleibt alles beim Stundenplan", async () => {
    setToken({ role: "dashboard", sub: "wall" });
    stubBackend({ children: [ANNA], childAccounts: [], breaks: [], breaksFail: true });

    renderDashboard();

    const table = await screen.findByRole("table", { name: "Heute und Morgen" });
    const annaToday = await within(table).findByRole("img", { name: /^Anna, Heute: Schule/ });
    expect(annaToday.closest(".day-cell")!.querySelector(".daybar-block")).not.toBeNull();
    expect(within(table).queryByText("Herbstferien")).toBeNull();
  });
});
