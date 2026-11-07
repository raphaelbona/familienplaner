import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

function tokenFor(role: string) {
  const payload = btoa(JSON.stringify({ family_id: "fam1", role, sub: "u1" }));
  return `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.${payload}.test`;
}

function stubFetch() {
  const fetchMock = vi.fn((url: string) => {
    const json = url.includes("/families/dashboard-user")
      ? null
      : url.includes("/health")
        ? { status: "ok", hosting_mode: "self_hosted" }
        : [];
    return Promise.resolve({ ok: true, status: 200, json: async () => json });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function calledUrls(fetchMock: ReturnType<typeof stubFetch>) {
  return fetchMock.mock.calls.map((c) => String(c[0]));
}

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("Route /kinder", () => {
  it.each(["child", "dashboard"])("leitet %s auf die Startseite um und laedt keine Kontodaten", async (role) => {
    localStorage.setItem("familienplaner_token", tokenFor(role));
    localStorage.setItem("familienplaner_family_id", "fam1");
    const fetchMock = stubFetch();

    render(
      <MemoryRouter initialEntries={["/kinder"]}>
        <App />
      </MemoryRouter>
    );

    await waitFor(() => expect(screen.queryByText("Wand-Dashboard")).not.toBeInTheDocument());
    expect(screen.queryByText("Neues Kind")).not.toBeInTheDocument();
    expect(screen.queryByText("Erwachsene")).not.toBeInTheDocument();
    const urls = calledUrls(fetchMock);
    expect(urls.some((u) => u.includes("/families/dashboard-user"))).toBe(false);
    expect(urls.some((u) => u.endsWith("/families/children") && role === "child")).toBe(false);
    expect(urls.some((u) => u.includes("/food-preferences"))).toBe(false);
  });

  it("zeigt die Kinderverwaltung fuer Eltern", async () => {
    localStorage.setItem("familienplaner_token", tokenFor("parent_admin"));
    stubFetch();

    render(
      <MemoryRouter initialEntries={["/kinder"]}>
        <App />
      </MemoryRouter>
    );

    expect(await screen.findByText("Neues Kind")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Wand-Dashboard" })).toBeInTheDocument();
  });

  it("laedt fuer das Wand-Dashboard weiterhin alle Kinder auf der Startseite", async () => {
    localStorage.setItem("familienplaner_token", tokenFor("dashboard"));
    const fetchMock = stubFetch();

    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>
    );

    await waitFor(() => expect(calledUrls(fetchMock).some((u) => u.endsWith("/families/children"))).toBe(true));
  });
});

describe("Route /dokumente", () => {
  it.each(["child", "dashboard"])("leitet %s auf die Startseite um und laedt keine Briefe", async (role) => {
    localStorage.setItem("familienplaner_token", tokenFor(role));
    localStorage.setItem("familienplaner_family_id", "fam1");
    const fetchMock = stubFetch();

    render(
      <MemoryRouter initialEntries={["/dokumente"]}>
        <App />
      </MemoryRouter>
    );

    await waitFor(() => expect(screen.queryByText(/Brief/i)).not.toBeInTheDocument());
    expect(calledUrls(fetchMock).some((u) => u.includes("/documents"))).toBe(false);
  });

  it("zeigt die Briefe fuer Eltern", async () => {
    localStorage.setItem("familienplaner_token", tokenFor("parent_admin"));
    localStorage.setItem("familienplaner_family_id", "fam1");
    const fetchMock = stubFetch();

    render(
      <MemoryRouter initialEntries={["/dokumente"]}>
        <App />
      </MemoryRouter>
    );

    await waitFor(() => expect(calledUrls(fetchMock).some((u) => u.includes("/documents"))).toBe(true));
  });
});

describe("Kinder-Login-Navigation im Wand-Dashboard", () => {
  it("blendet den Kind-Button aus, solange das Geraet nicht freigegeben ist", async () => {
    localStorage.setItem("familienplaner_token", tokenFor("dashboard"));
    stubFetch();
    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>
    );
    expect(await screen.findByText("Abmelden")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Kind/ })).not.toBeInTheDocument();
  });

  it("zeigt den Kind-Button auf einem freigegebenen Geraet", async () => {
    localStorage.setItem("familienplaner_token", tokenFor("dashboard"));
    localStorage.setItem("familienplaner_device_token", "tok");
    stubFetch();
    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>
    );
    expect(await screen.findByRole("button", { name: /Kind/ })).toBeInTheDocument();
  });
});
