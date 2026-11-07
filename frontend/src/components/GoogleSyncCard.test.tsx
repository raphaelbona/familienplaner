import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import GoogleSyncCard, { formatLastSync } from "./GoogleSyncCard";
import type { GoogleSyncStatus } from "../api/googleSync";

const MIA = "c-mia";
const PAPA = "u-papa";

function status(overrides: Partial<GoogleSyncStatus> = {}): GoogleSyncStatus {
  return {
    configured: true,
    connected: true,
    email: "papa@gmail.com",
    status: "ok",
    last_synced_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
    last_error: null,
    calendars: [
      { id: "papa@gmail.com", name: "Papa", selected: true, owner_user_ids: [] },
      { id: "schule@group", name: "Schule", selected: true, owner_user_ids: [MIA] },
      { id: "sport@group", name: "Sport", selected: false, owner_user_ids: [] },
    ],
    ...overrides,
  };
}

type Api = {
  status: GoogleSyncStatus;
  /** Optional per-route overrides: return a Response-like body or throw by returning {error}. */
  syncNow?: (() => { status?: number; detail?: string; headers?: Record<string, string> }) | null;
};

function stubApi(api: Api) {
  const calls: { path: string; init?: RequestInit }[] = [];
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    const path = String(url).replace(/^https?:\/\/[^/]+/, "");
    const method = init?.method ?? "GET";
    calls.push({ path: `${method} ${path}`, init });
    const ok = (body: unknown, code = 200) =>
      Promise.resolve({ ok: true, status: code, statusText: "", headers: new Headers(), json: async () => body });
    if (path === "/food-preferences/") {
      return ok([
        { user_id: PAPA, display_name: "Papa", likes: [], dislikes: [], allergies: [] },
        { user_id: MIA, display_name: "Mia", likes: [], dislikes: [], allergies: [] },
      ]);
    }
    if (path === "/families/children") return ok([{ id: MIA, avatar_key: "🦊" }]);
    if (path === "/calendar/sync/google/status") return ok(api.status);
    if (path === "/calendar/sync/google/connect") return ok({ authorization_url: "https://accounts.google.com/auth?x=1" });
    if (path === "/calendar/sync/google/calendars" && method === "PUT") {
      const sent = JSON.parse(String(init?.body)).calendars as { id: string; owner_user_ids: string[] }[];
      api.status = {
        ...api.status,
        calendars: api.status.calendars.map((c) => {
          const hit = sent.find((s) => s.id === c.id);
          return { ...c, selected: Boolean(hit), owner_user_ids: hit?.owner_user_ids ?? [] };
        }),
      };
      return ok(api.status);
    }
    if (path === "/calendar/sync/google/sync-now") {
      const failure = api.syncNow?.();
      if (failure) {
        return Promise.resolve({
          ok: false,
          status: failure.status ?? 500,
          statusText: "",
          headers: new Headers(failure.headers ?? {}),
          json: async () => ({ detail: failure.detail }),
        });
      }
      api.status = { ...api.status, last_synced_at: new Date().toISOString() };
      return ok(api.status);
    }
    if (path.startsWith("/calendar/sync/google?remove_events=")) {
      api.status = { configured: true, connected: false, email: null, status: null, last_synced_at: null, last_error: null, calendars: [] };
      return ok(undefined, 204);
    }
    return ok([]);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, calls, api };
}

function names(calls: { path: string }[]) {
  return calls.map((c) => c.path);
}

beforeEach(() => {
  localStorage.setItem("familienplaner_token", "x.e30.y");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  localStorage.clear();
});

describe("GoogleSyncCard: Zustände", () => {
  it("zeigt einen ruhigen Hinweis, wenn Google nicht eingerichtet ist", async () => {
    stubApi({ status: { configured: false, connected: false, email: null, status: null, last_synced_at: null, last_error: null, calendars: [] } });
    render(<GoogleSyncCard />);

    expect(await screen.findByText("Google ist noch nicht eingerichtet")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mit Google verbinden" })).not.toBeInTheDocument();
  });

  it("bietet 'Mit Google verbinden' an und startet den OAuth-Ablauf mit Cookies (credentials: include)", async () => {
    const onNavigate = vi.fn();
    const { calls } = stubApi({
      status: { configured: true, connected: false, email: null, status: null, last_synced_at: null, last_error: null, calendars: [] },
    });
    render(<GoogleSyncCard onNavigate={onNavigate} />);

    fireEvent.click(await screen.findByRole("button", { name: "Mit Google verbinden" }));

    await waitFor(() => expect(onNavigate).toHaveBeenCalledWith("https://accounts.google.com/auth?x=1"));
    const connect = calls.find((c) => c.path === "POST /calendar/sync/google/connect");
    expect(connect?.init?.credentials).toBe("include");
  });

  it("zeigt Verbindung, letzten Abgleich, Status und die Kalenderliste mit Mitglieder-Chips", async () => {
    stubApi({ status: status() });
    render(<GoogleSyncCard />);

    expect(await screen.findByText("Verbunden als papa@gmail.com")).toBeInTheDocument();
    expect(screen.getByText(/Zuletzt synchronisiert: vor 5 Min\./)).toBeInTheDocument();
    expect(screen.getByText("OK")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Jetzt synchronisieren" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Trennen" })).toBeInTheDocument();

    expect(screen.getByRole("checkbox", { name: "Papa" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Schule" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Sport" })).not.toBeChecked();

    const school = screen.getByTestId("calendar-row-schule@group");
    expect(within(school).getByRole("button", { name: /Mia/ })).toHaveAttribute("aria-pressed", "true");
    expect(within(school).getByRole("button", { name: /Papa/ })).toHaveAttribute("aria-pressed", "false");
    expect(within(school).queryByText("Ganze Familie")).not.toBeInTheDocument();
    expect(within(screen.getByTestId("calendar-row-papa@gmail.com")).getByText("Ganze Familie")).toBeInTheDocument();
    // not selected: no member chips
    expect(within(screen.getByTestId("calendar-row-sport@group")).queryByRole("button")).not.toBeInTheDocument();

    expect(screen.getByText(/Mia: Zahnarzt/)).toBeInTheDocument(); // title-tag hint
    expect(screen.getByText(/Belegt/)).toBeInTheDocument(); // parent-only events hint
    // holiday detection hint, including the known limitation and the name prefix
    const holidayHint = screen.getByText(/Ganztägige Termine mit/);
    expect(holidayHint).toHaveTextContent(/Ferien.*Schulfrei/);
    expect(holidayHint).toHaveTextContent(/Ferienwohnung/);
    expect(holidayHint).toHaveTextContent(/Mia: Ferien/);
  });

  it("zeigt bei needs_reauth 'Neu verbinden nötig' mit Button, der den Ablauf neu startet", async () => {
    const onNavigate = vi.fn();
    const { calls } = stubApi({
      status: status({ status: "needs_reauth", last_error: "Der Google-Zugriff ist abgelaufen oder wurde widerrufen. Bitte neu verbinden." }),
    });
    render(<GoogleSyncCard onNavigate={onNavigate} />);

    expect(await screen.findByText("Neu verbinden nötig")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Jetzt synchronisieren" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Neu verbinden" }));

    await waitFor(() => expect(onNavigate).toHaveBeenCalledWith("https://accounts.google.com/auth?x=1"));
    expect(calls.find((c) => c.path === "POST /calendar/sync/google/connect")?.init?.credentials).toBe("include");
  });

  it("zeigt die Fehlermeldung bei Status 'error'", async () => {
    stubApi({ status: status({ status: "error", last_error: "Nicht synchronisiert: Schule" }) });
    render(<GoogleSyncCard />);

    expect(await screen.findByText("Nicht synchronisiert: Schule")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Jetzt synchronisieren" })).toBeInTheDocument();
  });

  it("zeigt einen Fehler, wenn der Status nicht geladen werden kann, mit erneutem Versuch", async () => {
    let fail = true;
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        const path = String(url).replace(/^https?:\/\/[^/]+/, "");
        if (path === "/calendar/sync/google/status" && fail) {
          return Promise.resolve({ ok: false, status: 500, statusText: "", headers: new Headers(), json: async () => ({ detail: "Kaputt" }) });
        }
        return Promise.resolve({ ok: true, status: 200, statusText: "", headers: new Headers(), json: async () => (path.includes("status") ? status() : []) });
      })
    );
    render(<GoogleSyncCard />);

    expect(await screen.findByText("Kaputt")).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByText("Verbunden als papa@gmail.com")).toBeInTheDocument();
  });
});

describe("GoogleSyncCard: Kalenderauswahl speichern", () => {
  it("sendet nur die gewählten Kalender mit ihren Mitgliedern per PUT und gibt Rückmeldung", async () => {
    const { calls } = stubApi({ status: status() });
    const onEventsChanged = vi.fn();
    render(<GoogleSyncCard onEventsChanged={onEventsChanged} />);
    await screen.findByText("Verbunden als papa@gmail.com");

    const save = screen.getByRole("button", { name: "Speichern" });
    expect(save).toBeDisabled(); // nothing changed yet

    fireEvent.click(screen.getByRole("checkbox", { name: "Sport" })); // select Sport
    fireEvent.click(screen.getByRole("checkbox", { name: "Papa" })); // deselect the primary calendar
    const school = screen.getByTestId("calendar-row-schule@group");
    fireEvent.click(within(school).getByRole("button", { name: /Papa/ })); // add Papa
    fireEvent.click(within(school).getByRole("button", { name: /Mia/ })); // remove Mia
    fireEvent.click(within(screen.getByTestId("calendar-row-sport@group")).getByRole("button", { name: /Mia/ }));
    expect(save).toBeEnabled();
    fireEvent.click(save);

    expect(await screen.findByText(/Gespeichert/)).toBeInTheDocument();
    const put = calls.find((c) => c.path === "PUT /calendar/sync/google/calendars");
    expect(JSON.parse(String(put?.init?.body))).toEqual({
      calendars: [
        { id: "schule@group", owner_user_ids: [PAPA] },
        { id: "sport@group", owner_user_ids: [MIA] },
      ],
    });
    // the new selection is imported right away
    await waitFor(() => expect(names(calls)).toContain("POST /calendar/sync/google/sync-now"));
    await waitFor(() => expect(onEventsChanged).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();
  });

  it("zeigt 'Ganze Familie', sobald alle Mitglieder abgewählt sind", async () => {
    stubApi({ status: status() });
    render(<GoogleSyncCard />);
    await screen.findByText("Verbunden als papa@gmail.com");

    const school = screen.getByTestId("calendar-row-schule@group");
    fireEvent.click(within(school).getByRole("button", { name: /Mia/ }));

    expect(within(school).getByText("Ganze Familie")).toBeInTheDocument();
  });

  it("meldet einen Speicherfehler", async () => {
    const { fetchMock } = stubApi({ status: status() });
    render(<GoogleSyncCard />);
    await screen.findByText("Verbunden als papa@gmail.com");
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((url: string, init?: RequestInit) =>
      init?.method === "PUT"
        ? Promise.resolve({ ok: false, status: 404, statusText: "", headers: new Headers(), json: async () => ({ detail: "Familienmitglied nicht gefunden" }) })
        : original(url, init)
    );

    fireEvent.click(screen.getByRole("checkbox", { name: "Sport" }));
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    expect(await screen.findByText("Familienmitglied nicht gefunden")).toBeInTheDocument();
  });
});

describe("GoogleSyncCard: Jetzt synchronisieren", () => {
  it("startet den Abgleich, aktualisiert die Anzeige und lädt die Termine neu", async () => {
    const { calls } = stubApi({ status: status({ last_synced_at: new Date(Date.now() - 3 * 3600 * 1000).toISOString() }) });
    const onEventsChanged = vi.fn();
    render(<GoogleSyncCard onEventsChanged={onEventsChanged} />);
    expect(await screen.findByText(/vor 3 Std\./)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Jetzt synchronisieren" }));

    expect(await screen.findByText(/gerade eben/)).toBeInTheDocument();
    expect(names(calls)).toContain("POST /calendar/sync/google/sync-now");
    expect(onEventsChanged).toHaveBeenCalled();
  });

  it("zeigt die Meldung des Servers bei zu häufigem Abgleich (429)", async () => {
    stubApi({
      status: status(),
      syncNow: () => ({ status: 429, detail: "Bitte kurz warten, bevor du erneut synchronisierst.", headers: { "Retry-After": "20" } }),
    });
    render(<GoogleSyncCard />);

    fireEvent.click(await screen.findByRole("button", { name: "Jetzt synchronisieren" }));

    expect(await screen.findByText("Bitte kurz warten, bevor du erneut synchronisierst.")).toBeInTheDocument();
  });
});

describe("GoogleSyncCard: Trennen in zwei Schritten", () => {
  it("löscht erst nach Bestätigung und behält Termine standardmässig (remove_events=false)", async () => {
    const { calls } = stubApi({ status: status() });
    const onEventsChanged = vi.fn();
    render(<GoogleSyncCard onEventsChanged={onEventsChanged} />);

    fireEvent.click(await screen.findByRole("button", { name: "Trennen" }));
    expect(names(calls).some((c) => c.startsWith("DELETE"))).toBe(false); // step one only asks

    const checkbox = screen.getByRole("checkbox", { name: "Importierte Termine auch entfernen" });
    expect(checkbox).not.toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Wirklich trennen" }));

    await waitFor(() => expect(names(calls)).toContain("DELETE /calendar/sync/google?remove_events=false"));
    expect(await screen.findByRole("button", { name: "Mit Google verbinden" })).toBeInTheDocument();
    expect(onEventsChanged).toHaveBeenCalled();
  });

  it("entfernt die importierten Termine mit angekreuzter Box (remove_events=true)", async () => {
    const { calls } = stubApi({ status: status() });
    render(<GoogleSyncCard />);

    fireEvent.click(await screen.findByRole("button", { name: "Trennen" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Importierte Termine auch entfernen" }));
    fireEvent.click(screen.getByRole("button", { name: "Wirklich trennen" }));

    await waitFor(() => expect(names(calls)).toContain("DELETE /calendar/sync/google?remove_events=true"));
  });

  it("bricht ab, ohne etwas zu löschen", async () => {
    const { calls } = stubApi({ status: status() });
    render(<GoogleSyncCard />);

    fireEvent.click(await screen.findByRole("button", { name: "Trennen" }));
    fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));

    expect(screen.queryByRole("button", { name: "Wirklich trennen" })).not.toBeInTheDocument();
    expect(screen.getByText("Verbunden als papa@gmail.com")).toBeInTheDocument();
    expect(names(calls).some((c) => c.startsWith("DELETE"))).toBe(false);
  });
});

describe("formatLastSync", () => {
  const now = new Date(2026, 9, 4, 14, 30);

  it("formatiert relativ, dann als Uhrzeit, dann als Datum", () => {
    expect(formatLastSync(null, now)).toBe("noch nie");
    expect(formatLastSync(new Date(2026, 9, 4, 14, 29, 40).toISOString(), now)).toBe("gerade eben");
    expect(formatLastSync(new Date(2026, 9, 4, 14, 0).toISOString(), now)).toBe("vor 30 Min.");
    expect(formatLastSync(new Date(2026, 9, 4, 11, 15).toISOString(), now)).toBe("vor 3 Std.");
    expect(formatLastSync(new Date(2026, 9, 4, 0, 5).toISOString(), now)).toMatch(/^heute 00[:.]05$/);
    expect(formatLastSync(new Date(2026, 9, 2, 9, 5).toISOString(), now)).toMatch(/02\.10\.2026/);
  });
});
