import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Timetable from "./Timetable";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

function setToken(claims: Record<string, string>) {
  const payload = btoa(JSON.stringify({ family_id: "fam1", ...claims }));
  localStorage.setItem("familienplaner_token", `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.${payload}.test`);
  localStorage.setItem("familienplaner_family_id", "fam1");
}

function stubBackend() {
  const ok = (body: unknown) => Promise.resolve({ ok: true, status: 200, statusText: "", headers: new Headers(), json: async () => body });
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const { pathname } = new URL(url);
      if (pathname === "/school-breaks") {
        return ok([
          {
            id: "b1",
            title: "Herbstferien",
            kind: "ferien",
            start_date: "2999-10-12",
            end_date: "2999-10-23",
            child_user_ids: [],
            source: "manual",
          },
        ]);
      }
      if (/^\/(calendar|timetables|families)/.test(pathname)) return ok([]);
      return Promise.reject(new Error(`Unknown endpoint: ${url}`));
    })
  );
}

describe("Stundenplan: Ferien und freie Tage", () => {
  it("Eltern sehen die Liste mit Hinzufügen, Bearbeiten und Löschen", async () => {
    setToken({ role: "parent_admin", sub: "papa" });
    stubBackend();

    render(<Timetable />);

    expect(await screen.findByText("Herbstferien")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Ferien und freie Tage" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Ferien hinzufügen/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ferien bearbeiten: Herbstferien" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ferien löschen: Herbstferien" })).toBeInTheDocument();
  });

  it.each(["child", "dashboard"])("%s sieht die Liste nur lesend", async (role) => {
    setToken({ role, sub: "someone" });
    stubBackend();

    render(<Timetable />);

    expect(await screen.findByText("Herbstferien")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("12.10. – 23.10.2999")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /Ferien hinzufügen/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Ferien bearbeiten/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Ferien löschen/ })).toBeNull();
  });
});
