import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { localIsoDateOffset, todayIsoDate } from "../utils/date";
import { formatShortDate } from "../utils/dayBar";
import Dashboard from "./Dashboard";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("Dashboard", () => {
  it("zeigt eine Fehlermeldung, wenn das Backend nicht erreichbar ist", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("Netzwerkfehler"))
    );

    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/backend nicht erreichbar/i)).toBeInTheDocument();
    });
  });

  it("zeigt 'Kinder hinzufügen' für Parent mit null Kindern", async () => {
    // Mock token for parent user
    localStorage.setItem(
      "familienplaner_token",
      "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJmYW1pbHlfaWQiOiJ0ZXN0LWZhbWlseSIsInJvbGUiOiJwYXJlbnRfYWRtaW4ifQ.test"
    );

    const fetchMock = vi.fn((url: string) => {
      if (url.includes("/health")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ status: "ok", hosting_mode: "self_hosted" })
        });
      }
      if (url.includes("/families/children")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => []
        });
      }
      if (url.includes("/weather/forecast") || url.includes("/calendar/events")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => [] });
      }
      return Promise.reject(new Error("Unknown endpoint"));
    });

    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/Kinder hinzufügen/i)).toBeInTheDocument();
      expect(screen.getByText(/Lege jetzt die Konten deiner Kinder an/i)).toBeInTheDocument();
    });
  });

  it("zeigt Kinder-Übersicht für Parent mit Kindern", async () => {
    // Mock token for parent user
    localStorage.setItem(
      "familienplaner_token",
      "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJmYW1pbHlfaWQiOiJ0ZXN0LWZhbWlseSIsInJvbGUiOiJwYXJlbnRfYWRtaW4ifQ.test"
    );

    const fetchMock = vi.fn((url: string) => {
      if (url.includes("/health")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ status: "ok", hosting_mode: "self_hosted" })
        });
      }
      if (url.includes("/families/children")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => [
            { id: "child1", display_name: "Anna", avatar_key: "🦁" },
            { id: "child2", display_name: "Bob", avatar_key: "🐯" }
          ]
        });
      }
      if (url.includes("/timetables/") || url.includes("/weather/forecast") || url.includes("/calendar/events")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => [] });
      }
      return Promise.reject(new Error("Unknown endpoint"));
    });

    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/Familie heute/i)).toBeInTheDocument();
      expect(screen.getAllByText(/Anna/i).length).toBeGreaterThan(0);
      expect(screen.getAllByText(/Bob/i).length).toBeGreaterThan(0);
    });
  });

  it("zeigt das Kind-Dashboard mit Punkten, Aufgaben, Stundenplan und Terminen", async () => {
    const payload = btoa(JSON.stringify({ family_id: "fam1", role: "child", sub: "child1" }));
    localStorage.setItem("familienplaner_token", `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.${payload}.test`);
    localStorage.setItem("familienplaner_family_id", "fam1");

    const todayPythonWeekday = (new Date().getDay() + 6) % 7;
    const todayIsoDate = new Date().toISOString().slice(0, 10);
    const futureEvent = new Date(Date.now() + 86400000).toISOString();

    const fetchMock = vi.fn((url: string) => {
      if (url.includes("/health")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ status: "ok", hosting_mode: "self_hosted" }) });
      }
      if (url.includes("/families/fam1/child-accounts")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => [{ id: "child1", display_name: "Mia", avatar_key: "🦄" }]
        });
      }
      if (url.includes("/points/balance")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ balance: 42 }) });
      }
      if (url.includes("/earnings/balance")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ pending_chf: 0, approved_chf: 0, paid_out_chf: 0 })
        });
      }
      if (url.includes("/tasks/instances")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => [
            {
              id: "t1",
              due_date: todayIsoDate,
              status: "open",
              template: { title: "Zimmer aufräumen", kind: "basis", points_value: 0, bonus_multiplier: 1 }
            }
          ]
        });
      }
      if (url.includes("/timetables/child1")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => [
            // Anchored late in the day so the lesson is not yet over regardless of when this test runs.
            { id: "tt1", day_of_week: todayPythonWeekday, start_time: "23:00", end_time: "23:59", subject: "Deutsch", room: "101" }
          ]
        });
      }
      if (url.includes("/calendar/events")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => [
            { id: "e1", title: "Geburtstag", start: futureEvent, end: futureEvent, all_day: false, owner_user_id: null }
          ]
        });
      }
      if (url.includes("/weather/forecast")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => [] });
      }
      if (url.includes("/rewards")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => [] });
      }
      if (url.includes("/meal-plan/entries")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => [] });
      }
      return Promise.reject(new Error(`Unknown endpoint: ${url}`));
    });

    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/Hallo, Mia/i)).toBeInTheDocument();
      expect(screen.getByText("42")).toBeInTheDocument();
      expect(screen.getByText(/Zimmer aufräumen/i)).toBeInTheDocument();
      // Lessons are collapsed to a school block with its times (no subject list any more).
      expect(screen.getByRole("img", { name: /Heute: Schule 23:00 bis 23:59/ })).toBeInTheDocument();
      expect(screen.getByText(/Geburtstag/i)).toBeInTheDocument();
    });
  });

  it("zeigt Fallback für nicht-Parent Benutzer", async () => {
    // No token = not logged in, show fallback
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ status: "ok", hosting_mode: "self_hosted" })
      })
    );

    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText(/Nächste Schritte/i)).toBeInTheDocument();
      expect(screen.getByText(/Erkunde/i)).toBeInTheDocument();
      expect(screen.getByText(/im Menü unten/i)).toBeInTheDocument();
      // Should NOT show the old developer-facing text
      expect(screen.queryByText(/register-family/i)).not.toBeInTheDocument();
      // Should NOT show the parent-only "Kinder hinzufügen" card
      expect(screen.queryByText(/Kinder hinzufügen/i)).not.toBeInTheDocument();
    });
  });

  describe("Aufgaben-Kacheln (erledigen und zurücknehmen)", () => {
    type StoredInstance = {
      id: string;
      assignee_user_id: string;
      due_date: string;
      status: "open" | "completed" | "approved";
      template: Record<string, unknown>;
    };

    function setToken(claims: Record<string, string>) {
      const payload = btoa(JSON.stringify({ family_id: "fam1", ...claims }));
      localStorage.setItem("familienplaner_token", `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.${payload}.test`);
      localStorage.setItem("familienplaner_family_id", "fam1");
    }

    function instance(id: string, title: string, status: StoredInstance["status"], dueDate = todayIsoDate()) {
      return {
        id,
        assignee_user_id: "child1",
        due_date: dueDate,
        status,
        template: { title, kind: "basis", points_value: 0, bonus_multiplier: 1, money_value_chf: 0, icon: "🍽️" },
      } satisfies StoredInstance;
    }

    /** Stateful fake backend: complete/reopen mutate the stored instances like the real API. */
    function stubBackend(store: StoredInstance[], opts: { moveChangedToEnd?: boolean } = {}) {
      const posts: string[] = [];
      const ok = (body: unknown) => Promise.resolve({ ok: true, status: 200, json: async () => body });
      const fetchMock = vi.fn((url: string, init?: RequestInit) => {
        const { pathname, searchParams } = new URL(url);
        const action = pathname.match(/^\/tasks\/instances\/([^/]+)\/(complete|reopen)$/);
        if (action && init?.method === "POST") {
          posts.push(pathname);
          const found = store.find((i) => i.id === action[1])!;
          found.status = action[2] === "complete" ? "completed" : "open";
          if (opts.moveChangedToEnd) {
            store.splice(store.indexOf(found), 1);
            store.push(found); // like Postgres: an updated row comes back last
          }
          return ok(found);
        }
        if (pathname === "/tasks/instances") {
          const status = searchParams.get("status_filter");
          const assignee = searchParams.get("assignee_user_id");
          return ok(store.filter((i) => (!status || i.status === status) && (!assignee || i.assignee_user_id === assignee)));
        }
        if (pathname === "/health") return ok({ status: "ok", hosting_mode: "self_hosted" });
        if (pathname === "/families/children") return ok([{ id: "child1", display_name: "Mia", avatar_key: "🦄" }]);
        if (pathname === "/families/me") return ok({ name: "Muster" });
        if (pathname === "/families/fam1/child-accounts") return ok([{ id: "child1", display_name: "Mia", avatar_key: "🦄" }]);
        if (pathname === "/points/balance") return ok({ balance: 3 });
        if (pathname === "/earnings/balance") return ok({ pending_chf: 0, approved_chf: 0, paid_out_chf: 0 });
        if (/^\/(timetables|calendar|weather|rewards|meal-plan)/.test(pathname)) return ok([]);
        return Promise.reject(new Error(`Unknown endpoint: ${url}`));
      });
      vi.stubGlobal("fetch", fetchMock);
      return { posts };
    }

    it("Wand-Dashboard: Kachel antippen erledigt die Aufgabe, nochmals antippen nimmt sie zurück", async () => {
      setToken({ role: "dashboard", sub: "wall" });
      const { posts } = stubBackend([
        instance("i1", "Teller wegräumen", "open"),
        instance("i2", "Morgen-Aufgabe", "open", localIsoDateOffset(1)),
      ]);

      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      const openTile = await screen.findByRole("button", { name: "Teller wegräumen, offen, antippen wenn erledigt" });
      expect(screen.queryByText("Morgen-Aufgabe")).not.toBeInTheDocument();

      fireEvent.click(openTile);
      const doneTile = await screen.findByRole("button", {
        name: "Teller wegräumen, erledigt, antippen zum Zurücknehmen",
      });
      expect(posts).toEqual(["/tasks/instances/i1/complete"]);

      fireEvent.click(doneTile);
      await screen.findByRole("button", { name: "Teller wegräumen, offen, antippen wenn erledigt" });
      expect(posts).toEqual(["/tasks/instances/i1/complete", "/tasks/instances/i1/reopen"]);
    });

    it("Wand-Dashboard: Kacheln behalten ihren Platz, auch wenn das Backend die Reihenfolge ändert", async () => {
      setToken({ role: "dashboard", sub: "wall" });
      stubBackend(
        [instance("i1", "Aufgabe A", "open"), instance("i2", "Aufgabe B", "open"), instance("i3", "Aufgabe C", "open")],
        { moveChangedToEnd: true }
      );

      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      const order = () => screen.getAllByRole("button", { name: /^Aufgabe [ABC],/ }).map((b) => b.getAttribute("aria-label")!.slice(0, 9));
      await screen.findByRole("button", { name: /Aufgabe A, offen/ });
      expect(order()).toEqual(["Aufgabe A", "Aufgabe B", "Aufgabe C"]);

      fireEvent.click(screen.getByRole("button", { name: /Aufgabe A, offen/ }));
      await screen.findByRole("button", { name: /Aufgabe A, erledigt/ });

      expect(order()).toEqual(["Aufgabe A", "Aufgabe B", "Aufgabe C"]);
    });

    it("Wand-Dashboard: bestätigte Aufgabe ist gesperrt und löst keine Anfrage aus", async () => {
      setToken({ role: "dashboard", sub: "wall" });
      const { posts } = stubBackend([instance("i1", "Teller wegräumen", "approved")]);

      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      const tile = await screen.findByRole("button", { name: "Teller wegräumen, von Eltern bestätigt" });
      expect(tile).toBeDisabled();
      fireEvent.click(tile);
      expect(posts).toEqual([]);
    });

    it("Wand-Dashboard: zeigt den schlanken Banner", async () => {
      setToken({ role: "dashboard", sub: "wall" });
      stubBackend([]);

      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      const heading = await screen.findByRole("heading", { name: "Familien-Dashboard" });
      expect(heading.parentElement).toHaveClass("hero", "hero--slim");
    });

    it("Wand-Dashboard: zeigt die Uhrzeit im Banner, Kinder nicht", async () => {
      setToken({ role: "dashboard", sub: "wall" });
      stubBackend([]);

      const { unmount } = render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      const clock = await screen.findByTestId("wall-clock");
      expect(clock.querySelector("time")?.textContent).toMatch(/^\d{2}:\d{2}$/);
      expect(clock.parentElement).toHaveClass("hero--slim");
      unmount();

      setToken({ role: "child", sub: "child1" });
      stubBackend([]);
      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );
      await screen.findByRole("heading", { name: /Hallo/ });
      expect(screen.queryByTestId("wall-clock")).toBeNull();
    });

    it("Kind-Ansicht: zeigt alle heutigen Aufgaben als Kacheln und kann erledigen sowie zurücknehmen", async () => {
      setToken({ role: "child", sub: "child1" });
      const { posts } = stubBackend([
        instance("i1", "Teller wegräumen", "open"),
        instance("i2", "Zimmer aufräumen", "completed"),
        instance("i3", "Tisch decken", "approved"),
        instance("i4", "Morgen-Aufgabe", "open", localIsoDateOffset(1)),
      ]);

      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      await screen.findByText(/Hallo, Mia/i);
      expect(await screen.findByRole("button", { name: /Teller wegräumen, offen/ })).toBeEnabled();
      expect(screen.getByRole("button", { name: /Tisch decken, von Eltern bestätigt/ })).toBeDisabled();
      expect(screen.queryByText("Morgen-Aufgabe")).not.toBeInTheDocument();
      expect(screen.queryByText(/Wartet:/)).not.toBeInTheDocument();

      // Undo a completed task: no celebration banner.
      fireEvent.click(screen.getByRole("button", { name: /Zimmer aufräumen, erledigt/ }));
      await screen.findByRole("button", { name: /Zimmer aufräumen, offen/ });
      expect(posts).toEqual(["/tasks/instances/i2/reopen"]);
      expect(screen.queryByText(/Gut gemacht/)).not.toBeInTheDocument();

      // Completing shows the celebration.
      fireEvent.click(screen.getByRole("button", { name: /Teller wegräumen, offen/ }));
      await screen.findByRole("button", { name: /Teller wegräumen, erledigt/ });
      expect(posts).toEqual(["/tasks/instances/i2/reopen", "/tasks/instances/i1/complete"]);
      expect(await screen.findByText(/Gut gemacht, Mia/)).toBeInTheDocument();
    });

    it("Kind-Ansicht: ohne heutige Aufgaben steht 'Heute frei' da", async () => {
      setToken({ role: "child", sub: "child1" });
      stubBackend([instance("i4", "Morgen-Aufgabe", "open", localIsoDateOffset(1))]);

      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      // The "Heute und Morgen" day bar now also says "Heute frei" on a free day, so look inside the task card.
      const taskCard = (await screen.findByText("Heute zu tun")).closest(".card") as HTMLElement;
      expect(within(taskCard).getByText(/Heute frei/)).toBeInTheDocument();
    });
  });

  describe("Heute und Morgen (Tagesbalken)", () => {
    const NOW = new Date(2026, 9, 8, 7, 55); // Thursday 8.10.2026, 07:55 local
    const thursday = 3;
    const friday = 4;

    function localIso(day: number, hours: number, minutes = 0): string {
      return new Date(2026, 9, day, hours, minutes).toISOString();
    }

    function setToken(claims: Record<string, string>) {
      const payload = btoa(JSON.stringify({ family_id: "fam1", ...claims }));
      localStorage.setItem("familienplaner_token", `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.${payload}.test`);
      localStorage.setItem("familienplaner_family_id", "fam1");
    }

    const lessonsByChild: Record<string, unknown[]> = {
      child1: [
        { id: "a1", day_of_week: thursday, start_time: "08:20", end_time: "09:05", subject: "Deutsch", room: null },
        { id: "a2", day_of_week: thursday, start_time: "09:05", end_time: "11:50", subject: "Mathe", room: null },
        { id: "a3", day_of_week: friday, start_time: "07:30", end_time: "10:00", subject: "Sport", room: null },
        { id: "a4", day_of_week: friday, start_time: "13:30", end_time: "15:10", subject: "Werken", room: null },
      ],
      child2: [],
    };

    const events = [
      { id: "ev-anna", title: "Zahnarzt", start: localIso(8, 14, 30), end: localIso(8, 15, 0), all_day: false, source: "manual", owner_user_id: "child1" },
      { id: "ev-family", title: "Familienessen", start: localIso(9, 18, 0), end: localIso(9, 20, 0), all_day: false, source: "google", owner_user_id: null },
      { id: "ev-parent", title: "Elternabend", start: localIso(8, 19, 0), end: localIso(8, 21, 0), all_day: false, source: "manual", owner_user_id: "parent1" },
      { id: "ev-tt", title: "Spiegel-Lektion", start: localIso(8, 8, 20), end: localIso(8, 9, 5), all_day: false, source: "timetable", owner_user_id: "child1" },
    ];

    function stubBackend() {
      const ok = (body: unknown) => Promise.resolve({ ok: true, status: 200, json: async () => body });
      const kids = [
        { id: "child1", display_name: "Anna", avatar_key: "🦁" },
        { id: "child2", display_name: "Bob", avatar_key: "🐯" },
      ];
      vi.stubGlobal(
        "fetch",
        vi.fn((url: string) => {
          const { pathname } = new URL(url);
          if (pathname === "/health") return ok({ status: "ok", hosting_mode: "self_hosted" });
          if (pathname === "/families/children") return ok(kids);
          if (pathname === "/families/me") return ok({ name: "Muster" });
          if (pathname === "/families/fam1/child-accounts") return ok(kids);
          const timetable = pathname.match(/^\/timetables\/(.+)$/);
          if (timetable) return ok(lessonsByChild[timetable[1]] ?? []);
          if (pathname === "/calendar/events") return ok(events);
          if (pathname === "/points/balance") return ok({ balance: 0 });
          if (pathname === "/earnings/balance") return ok({ pending_chf: 0, approved_chf: 0, paid_out_chf: 0 });
          if (/^\/(tasks|weather|rewards|meal-plan)/.test(pathname)) return ok([]);
          return Promise.reject(new Error(`Unknown endpoint: ${url}`));
        })
      );
    }

    beforeEach(() => {
      vi.useFakeTimers({ toFake: ["Date"] });
      vi.setSystemTime(NOW);
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it("Wand-Dashboard: eine Zeile pro Kind plus Familie, mit Spaltenköpfen Heute und Morgen", async () => {
      setToken({ role: "dashboard", sub: "wall" });
      stubBackend();
      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      const table = await screen.findByRole("table", { name: "Heute und Morgen" });
      expect(within(table).getAllByRole("rowheader").map((h) => h.textContent)).toEqual([
        expect.stringContaining("Anna"),
        expect.stringContaining("Bob"),
        expect.stringContaining("Familie"),
      ]);
      const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent);
      expect(headers).toContain(`Heute · ${formatShortDate(NOW)}`);
      expect(headers).toContain("Heute · Do 8.10.");
      expect(headers).toContain("Morgen · Fr 9.10.");
    });

    it("Wand-Dashboard: Schulblöcke, Termine des Kindes und Familientermine stehen in den richtigen Zeilen", async () => {
      setToken({ role: "dashboard", sub: "wall" });
      stubBackend();
      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      const table = await screen.findByRole("table", { name: "Heute und Morgen" });
      await within(table).findByRole("img", { name: "Anna, Heute: Schule 08:20 bis 11:50, als Nächstes in 25 Min" });
      expect(
        within(table).getByRole("img", { name: "Anna, Morgen: Schule 07:30 bis 10:00 und 13:30 bis 15:10, Frühstunde, Nachmittag" })
      ).toBeInTheDocument();
      expect(within(table).getByRole("img", { name: "Bob, Heute: frei" })).toBeInTheDocument();
      expect(within(table).getByRole("img", { name: "Bob, Morgen: frei" })).toBeInTheDocument();
      expect(within(table).getByText("Heute frei")).toBeInTheDocument();
      expect(within(table).getByText("Morgen frei")).toBeInTheDocument();

      const rows = within(table).getAllByRole("row");
      const annaRow = rows.find((r) => within(r).queryByText("Anna"))!;
      const familyRow = rows.find((r) => within(r).queryByText("Familie"))!;
      expect(within(annaRow).getByText("Zahnarzt")).toBeInTheDocument();
      expect(within(annaRow).queryByText("Familienessen")).not.toBeInTheDocument();
      expect(within(familyRow).getByText("Familienessen")).toBeInTheDocument();
      expect(within(familyRow).getByRole("img", { name: "Familie, Heute: nichts geplant" })).toBeInTheDocument();

      // Events of parents and the auto-generated timetable mirrors are not shown at all.
      expect(screen.queryByText("Elternabend")).not.toBeInTheDocument();
      expect(screen.queryByText("Spiegel-Lektion")).not.toBeInTheDocument();
      // No more plain text lines like "Deutsch (08:20)".
      expect(screen.queryByText(/Deutsch/)).not.toBeInTheDocument();
      expect(screen.queryByText(/nichts mehr geplant/)).not.toBeInTheDocument();
    });

    it("Kind-Ansicht: zwei Tagesabschnitte Heute und Morgen mit Balken, Tags und Terminen", async () => {
      setToken({ role: "child", sub: "child1" });
      stubBackend();
      render(
        <MemoryRouter>
          <Dashboard />
        </MemoryRouter>
      );

      expect(await screen.findByRole("heading", { name: "Heute Do 8.10." })).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Morgen Fr 9.10." })).toBeInTheDocument();

      expect(await screen.findByRole("img", { name: /Anna, Heute: Schule 08:20 bis 11:50/ })).toBeInTheDocument();
      expect(
        await screen.findByRole("img", { name: /Anna, Morgen: Schule 07:30 bis 10:00 und 13:30 bis 15:10, Frühstunde, Nachmittag/ })
      ).toBeInTheDocument();
      expect(screen.getByText("Frühstunde")).toBeInTheDocument();
      expect(screen.getByText("Nachmittag")).toBeInTheDocument();
      expect(screen.getByText("Zahnarzt")).toBeInTheDocument();
      expect(screen.getByText("Familienessen")).toBeInTheDocument(); // family events belong to every child
      expect(screen.queryByText("Elternabend")).not.toBeInTheDocument();
    });
  });
});
