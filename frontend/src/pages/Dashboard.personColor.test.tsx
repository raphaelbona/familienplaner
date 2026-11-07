import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { todayIsoDate } from "../utils/date";
import Dashboard from "./Dashboard";

// jsdom normalises hex colors to rgb(); #e2793d is palette index 0 (orange), #3f89a3 index 1 (blue).
const ORANGE = "rgb(226, 121, 61)";
const BLUE = "rgb(63, 137, 163)";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

type Kid = { id: string; display_name: string; avatar_key: string; color_index?: number };

function setToken(claims: Record<string, string>) {
  const payload = btoa(JSON.stringify({ family_id: "fam1", ...claims }));
  localStorage.setItem("familienplaner_token", `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.${payload}.test`);
  localStorage.setItem("familienplaner_family_id", "fam1");
}

function stubBackend(opts: { children: Kid[]; childAccounts: Kid[] }) {
  const ok = (body: unknown) => Promise.resolve({ ok: true, status: 200, json: async () => body });
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const { pathname, searchParams } = new URL(url);
      if (pathname === "/health") return ok({ status: "ok", hosting_mode: "self_hosted" });
      if (pathname === "/families/children") return ok(opts.children);
      if (pathname === "/families/me") return ok({ name: "Muster" });
      if (pathname === "/families/fam1/child-accounts") return ok(opts.childAccounts);
      if (pathname === "/points/balance") return ok({ balance: 0 });
      if (pathname === "/earnings/balance") return ok({ pending_chf: 0, approved_chf: 0, paid_out_chf: 0 });
      if (pathname === "/tasks/instances") {
        const assignee = searchParams.get("assignee_user_id");
        // One open task today for every child so the week strip has a colored "open" dot.
        const ids = assignee ? [assignee] : [...opts.children, ...opts.childAccounts].map((k) => k.id);
        return ok(
          ids.map((id) => ({
            id: `i-${id}`,
            assignee_user_id: id,
            due_date: todayIsoDate(),
            status: "open",
            template: { title: "Zimmer", kind: "basis", points_value: 0, bonus_multiplier: 1, money_value_chf: 0, icon: "🧹" },
          }))
        );
      }
      if (/^\/(timetables|calendar|weather|rewards|meal-plan)/.test(pathname)) return ok([]);
      return Promise.reject(new Error(`Unknown endpoint: ${url}`));
    })
  );
}

function renderDashboard() {
  return render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>
  );
}

const ANNA: Kid = { id: "child1", display_name: "Anna", avatar_key: "🦁", color_index: 0 };
const NOAH: Kid = { id: "child2", display_name: "Noah", avatar_key: "🐯", color_index: 1 };

async function wallTileColor(name: string): Promise<string> {
  const tileName = await screen.findByText(name, { selector: ".family-tile-name" });
  return (tileName.closest(".family-tile") as HTMLElement).style.borderTopColor;
}

describe("Personenfarbe: ein Kind hat überall dieselbe Farbe", () => {
  it("Wand-Dashboard nutzt color_index vom Server, nicht die Listenposition", async () => {
    setToken({ role: "dashboard", sub: "wall" });
    // Only one child is listed, but it is the family's second child (color_index 1).
    stubBackend({ children: [NOAH], childAccounts: [] });

    renderDashboard();

    expect(await wallTileColor("Noah")).toBe(BLUE);
    const table = await screen.findByRole("table", { name: "Heute und Morgen" });
    const avatar = within(table).getByText("🐯");
    expect(avatar.style.borderColor).toBe(BLUE);
  });

  it("Wand-Dashboard fällt ohne color_index auf die Listenposition zurück", async () => {
    setToken({ role: "dashboard", sub: "wall" });
    const bare = ({ color_index: _ignored, ...rest }: Kid) => rest as Kid; // eslint-disable-line @typescript-eslint/no-unused-vars
    stubBackend({ children: [bare(ANNA), bare(NOAH)], childAccounts: [] });

    renderDashboard();

    expect(await wallTileColor("Anna")).toBe(ORANGE);
    expect(await wallTileColor("Noah")).toBe(BLUE);
  });

  it("Kinder-Board: Wochenleiste und Avatar haben dieselbe Farbe wie auf dem Wand-Dashboard", async () => {
    // Wall dashboard colour for Noah ...
    setToken({ role: "dashboard", sub: "wall" });
    stubBackend({ children: [ANNA, NOAH], childAccounts: [] });
    const wall = renderDashboard();
    const wallColor = await wallTileColor("Noah");
    wall.unmount();
    expect(wallColor).toBe(BLUE);

    // ... equals Noah's own board, where the child only sees itself but still gets the family-wide index.
    setToken({ role: "child", sub: "child2" });
    stubBackend({ children: [], childAccounts: [NOAH] });
    renderDashboard();

    await screen.findByText("Hallo, Noah!");
    expect(screen.getByTestId("hero-avatar").style.borderColor).toBe(wallColor);

    const weekCard = (await screen.findByText("Meine Woche")).closest(".card") as HTMLElement;
    const colored = Array.from(weekCard.querySelectorAll<HTMLElement>("span")).filter((el) =>
      el.style.border.includes(BLUE)
    );
    expect(colored.length).toBeGreaterThan(0);
    expect(weekCard.innerHTML).not.toContain("var(--accent)");
  });

  it("Kinder-Board ohne color_index nutzt die erste Personenfarbe", async () => {
    setToken({ role: "child", sub: "child1" });
    const { color_index: _ignored, ...bare } = ANNA; // eslint-disable-line @typescript-eslint/no-unused-vars
    stubBackend({ children: [], childAccounts: [bare] });

    renderDashboard();

    await screen.findByText("Hallo, Anna!");
    expect(screen.getByTestId("hero-avatar").style.borderColor).toBe(ORANGE);
  });
});
