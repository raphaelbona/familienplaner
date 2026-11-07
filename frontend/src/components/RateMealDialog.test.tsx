import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import RateMealDialog from "./RateMealDialog";
import StarRating from "./StarRating";
import { useState } from "react";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

function login(role: string, sub: string) {
  const payload = btoa(JSON.stringify({ family_id: "fam1", role, sub }));
  localStorage.setItem("familienplaner_token", `eyJhbGciOiJIUzI1NiJ9.${payload}.test`);
}

const entry = { recipe_id: "r1", recipe_name: "Lasagne", date: "2026-09-10" };

function Controlled() {
  const [value, setValue] = useState(0);
  return (
    <>
      <StarRating value={value} onChange={setValue} />
      <output data-testid="value">{value}</output>
    </>
  );
}

describe("StarRating", () => {
  it("hat beschriftete Sterne und wählt per Klick", () => {
    render(<Controlled />);
    fireEvent.click(screen.getByRole("radio", { name: "3 von 5 Sternen" }));
    expect(screen.getByTestId("value")).toHaveTextContent("3");
    expect(screen.getByRole("radio", { name: "3 von 5 Sternen" })).toHaveAttribute("aria-checked", "true");
  });

  it("lässt sich mit Pfeiltasten bedienen und bleibt in 1 bis 5", () => {
    render(<Controlled />);
    const first = screen.getByRole("radio", { name: "1 von 5 Sternen" });
    first.focus();
    fireEvent.keyDown(first, { key: "ArrowRight" });
    expect(screen.getByTestId("value")).toHaveTextContent("2");
    expect(screen.getByRole("radio", { name: "2 von 5 Sternen" })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole("radio", { name: "2 von 5 Sternen" }), { key: "ArrowLeft" });
    fireEvent.keyDown(screen.getByRole("radio", { name: "1 von 5 Sternen" }), { key: "ArrowLeft" });
    expect(screen.getByTestId("value")).toHaveTextContent("1");
  });
});

describe("RateMealDialog", () => {
  it("Kind bewertet sich selbst ohne user_id, mit grossen Sternen", async () => {
    login("child", "c1");
    const fetchMock = vi.fn(() => Promise.resolve({ ok: true, status: 201, json: async () => ({ id: "x", stars: 4 }) }));
    vi.stubGlobal("fetch", fetchMock);
    const onRated = vi.fn();

    render(<RateMealDialog entry={entry} isParent={false} onClose={() => {}} onRated={onRated} />);

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(document.querySelector(".star-rating--large")).not.toBeNull();
    expect(screen.getByRole("button", { name: "Bewertung speichern" })).toBeDisabled();

    fireEvent.click(screen.getByRole("radio", { name: "4 von 5 Sternen" }));
    fireEvent.click(screen.getByRole("button", { name: "Bewertung speichern" }));

    await waitFor(() => expect(onRated).toHaveBeenCalled());
    const [url, options] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/recipes\/r1\/ratings$/);
    expect(JSON.parse(options.body as string)).toEqual({ stars: 4, cooked_on: "2026-09-10" });
    expect(await screen.findByText("Danke für deine Bewertung!")).toBeInTheDocument();
  });

  it("Elternteil wählt das Mitglied und sendet dessen user_id", async () => {
    login("parent_admin", "p1");
    const fetchMock = vi.fn((url: string, options?: RequestInit) => {
      if (url.endsWith("/food-preferences/")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => [
            { user_id: "p1", display_name: "Papa", likes: [], dislikes: [], allergies: [] },
            { user_id: "c1", display_name: "Lea", likes: [], dislikes: [], allergies: [] }
          ]
        });
      }
      if (url.endsWith("/families/children")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => [{ id: "c1", avatar_key: "🦊" }] });
      }
      if (options?.method === "POST") return Promise.resolve({ ok: true, status: 201, json: async () => ({}) });
      return Promise.reject(new Error(`Unknown endpoint: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<RateMealDialog entry={entry} isParent onClose={() => {}} onRated={() => {}} />);

    fireEvent.change(await screen.findByLabelText("Wer bewertet?"), { target: { value: "c1" } });
    fireEvent.click(screen.getByRole("radio", { name: "5 von 5 Sternen" }));
    fireEvent.click(screen.getByRole("button", { name: "Bewertung speichern" }));

    expect(await screen.findByText("Bewertung von Lea gespeichert.")).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([, o]) => (o as RequestInit | undefined)?.method === "POST")!;
    expect(JSON.parse((post[1] as RequestInit).body as string)).toEqual({
      stars: 5,
      cooked_on: "2026-09-10",
      user_id: "c1"
    });
  });

  it("behält den Tastaturfokus im Dialog nach dem Speichern", async () => {
    login("child", "c1");
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: true, status: 201, json: async () => ({}) })));
    render(<RateMealDialog entry={entry} isParent={false} onClose={() => {}} onRated={() => {}} />);

    fireEvent.click(screen.getByRole("radio", { name: "4 von 5 Sternen" }));
    const save = screen.getByRole("button", { name: "Bewertung speichern" });
    save.focus();
    fireEvent.click(save);

    const notice = await screen.findByText("Danke für deine Bewertung!");
    await waitFor(() => expect(screen.getByRole("dialog").contains(document.activeElement)).toBe(true));
    expect(notice).toHaveFocus();
  });

  it("Tab holt verlorenen Fokus zurück in den Dialog (Shift+Tab ans Ende)", () => {
    login("child", "c1");
    render(<RateMealDialog entry={entry} isParent={false} onClose={() => {}} onRated={() => {}} />);
    const dialog = screen.getByRole("dialog");

    (document.activeElement as HTMLElement).blur();
    expect(document.body).toHaveFocus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Schliessen" }));

    (document.activeElement as HTMLElement).blur();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Fertig" }));
  });

  it("zeigt Fehler und schliesst mit Esc", async () => {
    login("child", "c1");
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve({ ok: false, status: 404, statusText: "x", json: async () => ({ detail: "Recipe not found" }) }))
    );
    const onClose = vi.fn();
    render(<RateMealDialog entry={entry} isParent={false} onClose={onClose} onRated={() => {}} />);

    fireEvent.click(screen.getByRole("radio", { name: "2 von 5 Sternen" }));
    fireEvent.click(screen.getByRole("button", { name: "Bewertung speichern" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Recipe not found");

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
