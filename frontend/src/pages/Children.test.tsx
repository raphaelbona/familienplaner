import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Children from "./Children";

function tokenFor(role: string) {
  const payload = btoa(JSON.stringify({ family_id: "fam1", role, sub: "u1" }));
  return `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.${payload}.test`;
}

function stubApi(children: unknown[]) {
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    const path = String(url).replace(/^https?:\/\/[^/]+/, "");
    const method = init?.method ?? "GET";
    let body: unknown = [];
    if (path === "/families/children" && method === "GET") body = children;
    else if (path === "/families/dashboard-user") body = null;
    else if (path === "/points/balance") body = { balance: 0 };
    else if (path === "/earnings/balance") body = { pending_approval: 0, approved: 0 };
    else if (path.endsWith("/unlock")) {
      (children[0] as { pin_locked_until: string | null }).pin_locked_until = null;
      return Promise.resolve({ ok: true, status: 204, statusText: "", json: async () => undefined });
    }
    return Promise.resolve({ ok: true, status: 200, statusText: "", json: async () => body });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("Kinder-Seite: PIN-Sperre", () => {
  it("zeigt bei gesperrtem Kind Hinweis und Entsperren-Button, der die Sperre aufhebt", async () => {
    localStorage.setItem("familienplaner_token", tokenFor("parent_admin"));
    const until = new Date(Date.now() + 10 * 60 * 1000).toISOString();
    const fetchMock = stubApi([
      { id: "c1", display_name: "Anna", birth_year: null, avatar_key: null, pin_locked_until: until },
    ]);

    render(
      <MemoryRouter>
        <Children />
      </MemoryRouter>
    );

    expect(await screen.findByText(/Gesperrt bis \d{2}[:.]\d{2}/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Entsperren" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          (c) => String(c[0]).endsWith("/families/children/c1/unlock") && (c[1] as RequestInit).method === "POST"
        )
      ).toBe(true)
    );
    await waitFor(() => expect(screen.queryByText(/Gesperrt bis/)).not.toBeInTheDocument());
  });

  it("zeigt nichts fuer nicht gesperrte Kinder", async () => {
    localStorage.setItem("familienplaner_token", tokenFor("parent_admin"));
    stubApi([{ id: "c1", display_name: "Anna", birth_year: null, avatar_key: null, pin_locked_until: null }]);

    render(
      <MemoryRouter>
        <Children />
      </MemoryRouter>
    );

    expect(await screen.findByText("Anna")).toBeInTheDocument();
    expect(screen.queryByText(/Gesperrt bis/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Entsperren" })).not.toBeInTheDocument();
  });
});
