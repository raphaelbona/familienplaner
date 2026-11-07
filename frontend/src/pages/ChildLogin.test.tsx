import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import ChildLogin from "./ChildLogin";
import Login from "./Login";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/kind-login" element={<ChildLogin />} />
        <Route path="/login" element={<Login />} />
      </Routes>
    </MemoryRouter>
  );
}

function stubFetch(
  handler: (url: string, init?: RequestInit) => { status: number; body: unknown; headers?: Record<string, string> }
) {
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    const { status, body, headers } = handler(String(url), init);
    return Promise.resolve({
      ok: status >= 200 && status < 300,
      status,
      statusText: "x",
      headers: new Headers(headers),
      json: async () => body,
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const ANNA = { id: "c1", display_name: "Anna", avatar_key: null, role: "child" };

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("Kinder-Login auf nicht freigegebenen Geraeten", () => {
  it("leitet ohne Geraete-Token zum Eltern-Login um und fragt keine Kinder ab", async () => {
    localStorage.setItem("familienplaner_family_id", "fam1");
    const fetchMock = stubFetch(() => ({ status: 200, body: [] }));

    renderAt("/kind-login");

    expect(await screen.findByRole("heading", { name: "Anmelden" })).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByText(/Wer nutzt das Tablet/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Kinder-Login/)).not.toBeInTheDocument();
  });

  it("blendet den Kinder-Link auf der Login-Seite aus, solange das Geraet nicht freigegeben ist", () => {
    renderAt("/login");
    expect(screen.queryByText(/Hier anmelden/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Kind/ })).not.toBeInTheDocument();
  });

  it("zeigt den Kinder-Link auf der Login-Seite fuer ein freigegebenes Geraet", () => {
    localStorage.setItem("familienplaner_device_token", "tok");
    renderAt("/login");
    expect(screen.getByRole("link", { name: /Hier anmelden/ })).toBeInTheDocument();
  });
});

describe("Kinder-Login auf freigegebenen Geraeten", () => {
  function pair() {
    localStorage.setItem("familienplaner_family_id", "fam1");
    localStorage.setItem("familienplaner_device_token", "tok-123");
  }

  it("zeigt die Auswahl und sendet das Geraete-Token", async () => {
    pair();
    const fetchMock = stubFetch(() => ({ status: 200, body: [ANNA] }));

    renderAt("/kind-login");

    expect(await screen.findByText("Anna")).toBeInTheDocument();
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/families/fam1/pin-accounts");
    expect(new Headers((init as RequestInit).headers).get("X-Device-Token")).toBe("tok-123");
  });

  it("loescht das Token und wechselt zum Eltern-Login, wenn die Freigabe widerrufen wurde (401)", async () => {
    pair();
    localStorage.setItem("familienplaner_device_id", "d1");
    stubFetch(() => ({ status: 401, body: { detail: "Gerät nicht freigegeben" } }));

    renderAt("/kind-login");

    expect(await screen.findByRole("heading", { name: "Anmelden" })).toBeInTheDocument();
    expect(localStorage.getItem("familienplaner_device_token")).toBeNull();
    expect(localStorage.getItem("familienplaner_device_id")).toBeNull();
    // the family id stays so the parent login keeps working as before
    expect(localStorage.getItem("familienplaner_family_id")).toBe("fam1");
  });

  it("sendet das Geraete-Token beim PIN-Login und behaelt es bei falscher PIN", async () => {
    pair();
    const fetchMock = stubFetch((url) =>
      url.includes("/pin-accounts") ? { status: 200, body: [ANNA] } : { status: 401, body: { detail: "PIN falsch" } }
    );

    renderAt("/kind-login");
    fireEvent.click(await screen.findByText("Anna"));
    fireEvent.change(screen.getByLabelText("PIN"), { target: { value: "1234" } });
    fireEvent.click(screen.getByRole("button", { name: "Anmelden" }));

    expect(await screen.findByText("PIN falsch")).toBeInTheDocument();
    const pinCall = fetchMock.mock.calls.find((c) => String(c[0]).endsWith("/auth/pin"))!;
    expect(new Headers((pinCall[1] as RequestInit).headers).get("X-Device-Token")).toBe("tok-123");
    expect(localStorage.getItem("familienplaner_device_token")).toBe("tok-123");
  });

  it("zeigt bei 429 die Sperr-Meldung, sperrt den Button und behaelt das Geraete-Token", async () => {
    pair();
    const fetchMock = stubFetch((url) =>
      url.includes("/pin-accounts")
        ? { status: 200, body: [ANNA] }
        : {
            status: 429,
            body: { detail: "Zu viele Versuche. Bitte in 5 Minuten erneut versuchen." },
            headers: { "Retry-After": "300" },
          }
    );

    renderAt("/kind-login");
    fireEvent.click(await screen.findByText("Anna"));
    fireEvent.change(screen.getByLabelText("PIN"), { target: { value: "1234" } });
    fireEvent.click(screen.getByRole("button", { name: "Anmelden" }));

    expect(await screen.findByText(/Zu viele Versuche. Bitte in 5 Minuten/)).toBeInTheDocument();
    expect(localStorage.getItem("familienplaner_device_token")).toBe("tok-123");
    expect(screen.queryByRole("heading", { name: "Anmelden" })).not.toBeInTheDocument();
    // even with a full PIN typed the submit button stays disabled while locked
    fireEvent.change(screen.getByLabelText("PIN"), { target: { value: "4321" } });
    expect(screen.getByRole("button", { name: "Anmelden" })).toBeDisabled();
    const pinCalls = fetchMock.mock.calls.filter((c) => String(c[0]).endsWith("/auth/pin"));
    expect(pinCalls).toHaveLength(1);
  });

  it("gibt den Button nach Ablauf der Retry-After-Zeit wieder frei", async () => {
    pair();
    let first = true;
    stubFetch((url) => {
      if (url.includes("/pin-accounts")) return { status: 200, body: [ANNA] };
      if (first) {
        first = false;
        return {
          status: 429,
          body: { detail: "Zu viele Versuche. Bitte in 1 Minute erneut versuchen." },
          headers: { "Retry-After": "1" },
        };
      }
      return { status: 401, body: { detail: "PIN falsch" } };
    });

    renderAt("/kind-login");
    fireEvent.click(await screen.findByText("Anna"));
    fireEvent.change(screen.getByLabelText("PIN"), { target: { value: "1234" } });
    fireEvent.click(screen.getByRole("button", { name: "Anmelden" }));
    expect(await screen.findByText(/Zu viele Versuche/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("PIN"), { target: { value: "1234" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Anmelden" })).toBeEnabled(), { timeout: 3000 });
    expect(screen.queryByText(/Zu viele Versuche/)).not.toBeInTheDocument();
  });

  it("loescht das Token, wenn /auth/pin das Geraet ablehnt", async () => {
    pair();
    stubFetch((url) =>
      url.includes("/pin-accounts")
        ? { status: 200, body: [ANNA] }
        : { status: 401, body: { detail: "Gerät nicht freigegeben" } }
    );

    renderAt("/kind-login");
    fireEvent.click(await screen.findByText("Anna"));
    fireEvent.change(screen.getByLabelText("PIN"), { target: { value: "1234" } });
    fireEvent.click(screen.getByRole("button", { name: "Anmelden" }));

    await waitFor(() => expect(localStorage.getItem("familienplaner_device_token")).toBeNull());
    expect(await screen.findByRole("heading", { name: "Anmelden" })).toBeInTheDocument();
  });

  it("behaelt das Geraete-Token nach dem Abmelden (clearToken)", async () => {
    pair();
    localStorage.setItem("familienplaner_token", "jwt");
    const { clearToken } = await import("../api/client");
    clearToken();
    expect(localStorage.getItem("familienplaner_device_token")).toBe("tok-123");
    expect(localStorage.getItem("familienplaner_family_id")).toBe("fam1");
  });
});
