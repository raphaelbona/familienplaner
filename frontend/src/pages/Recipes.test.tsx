import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Recipes from "./Recipes";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

function renderAs(role: string) {
  const payload = btoa(JSON.stringify({ family_id: "fam1", role, sub: "u1" }));
  localStorage.setItem("familienplaner_token", `eyJhbGciOiJIUzI1NiJ9.${payload}.test`);
  const fetchMock = vi.fn(() => Promise.resolve({ ok: true, status: 200, json: async () => [] }));
  vi.stubGlobal("fetch", fetchMock);
  render(
    <MemoryRouter>
      <Recipes />
    </MemoryRouter>
  );
  return waitFor(() => expect(fetchMock).toHaveBeenCalled());
}

describe("Recipes Finder-Link", () => {
  it("zeigt Eltern den Link zu 'Rezepte finden'", async () => {
    await renderAs("parent_member");
    expect(screen.getByRole("link", { name: /Neue Rezepte finden/ })).toHaveAttribute("href", "/rezepte/finden");
  });

  it("zeigt Kindern keinen toten Link", async () => {
    await renderAs("child");
    expect(screen.queryByRole("link", { name: /Rezepte finden/ })).not.toBeInTheDocument();
  });
});
