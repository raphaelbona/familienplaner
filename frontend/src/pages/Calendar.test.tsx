import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Calendar from "./Calendar";

function tokenFor(role: string) {
  const payload = btoa(JSON.stringify({ family_id: "fam1", role, sub: "u1" }));
  return `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.${payload}.test`;
}

const googleEvent = {
  id: "e1",
  title: "Zahnarzt",
  start: "2026-10-10T08:00:00Z",
  end: "2026-10-10T09:00:00Z",
  all_day: false,
  source: "google",
  owner_user_id: null,
  recurrence_rule: null,
};

function stubApi(events: unknown[]) {
  const fetchMock = vi.fn((url: string) => {
    const path = String(url).replace(/^https?:\/\/[^/]+/, "");
    let body: unknown = [];
    if (path === "/calendar/events") body = events;
    else if (path === "/calendar/sync/google/status") {
      body = { configured: false, connected: false, email: null, status: null, last_synced_at: null, last_error: null, calendars: [] };
    }
    return Promise.resolve({ ok: true, status: 200, statusText: "", headers: new Headers(), json: async () => body });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("Kalender-Seite: Google-Sync", () => {
  it("zeigt für Eltern die Google-Karte und weiterhin das CalDAV-Formular", async () => {
    localStorage.setItem("familienplaner_token", tokenFor("parent_admin"));
    stubApi([]);

    render(
      <MemoryRouter>
        <Calendar />
      </MemoryRouter>
    );

    expect(await screen.findByText("Google ist noch nicht eingerichtet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "CalDAV verbinden" })).toBeInTheDocument();
  });

  it("zeigt nach dem Google-Redirect den Erfolgs- bzw. Fehlerhinweis", async () => {
    localStorage.setItem("familienplaner_token", tokenFor("parent_admin"));
    stubApi([]);

    const { unmount } = render(
      <MemoryRouter initialEntries={["/kalender?google=connected"]}>
        <Calendar />
      </MemoryRouter>
    );
    expect(await screen.findByText("Google-Kalender erfolgreich verbunden.")).toBeInTheDocument();
    unmount();

    render(
      <MemoryRouter initialEntries={["/kalender?google=error&message=Ung%C3%BCltiger%20Code"]}>
        <Calendar />
      </MemoryRouter>
    );
    expect(await screen.findByText("Ungültiger Code")).toBeInTheDocument();
  });

  it("zeigt Kindern keine Sync-Karte, markiert aber importierte Google-Termine", async () => {
    localStorage.setItem("familienplaner_token", tokenFor("child"));
    const fetchMock = stubApi([googleEvent]);

    render(
      <MemoryRouter>
        <Calendar />
      </MemoryRouter>
    );

    expect(await screen.findByText("Zahnarzt")).toBeInTheDocument();
    expect(screen.getByText("Google")).toBeInTheDocument();
    expect(screen.queryByText("Kalender-Sync")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("/calendar/sync/google"))).toBe(false)
    );
  });
});
