import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import FoodPreferences from "./FoodPreferences";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

function stubApi(initial = { likes: ["Nudeln"], dislikes: ["Pilze"], allergies: ["Nüsse"] }) {
  const puts: unknown[] = [];
  const fetchMock = vi.fn((url: string, options?: RequestInit) => {
    if (url.endsWith("/food-preferences/members/u1")) {
      if (options?.method === "PUT") {
        const body = JSON.parse(options.body as string);
        puts.push(body);
        return Promise.resolve({ ok: true, status: 200, json: async () => body });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => initial });
    }
    return Promise.reject(new Error(`Unknown endpoint: ${url}`));
  });
  vi.stubGlobal("fetch", fetchMock);
  return puts;
}

describe("FoodPreferences", () => {
  it("zeigt Vorlieben, Abneigungen und Allergien; Allergien mit Textlabel", async () => {
    stubApi();
    render(<FoodPreferences userId="u1" />);

    expect(await screen.findByText("Nudeln")).toBeInTheDocument();
    expect(screen.getByText("Pilze")).toBeInTheDocument();
    expect(screen.getByText("Nüsse")).toBeInTheDocument();
    expect(screen.getByText("Allergie:")).toBeInTheDocument();
  });

  it("speichert beim Hinzufügen das ganze Set per PUT", async () => {
    const puts = stubApi();
    render(<FoodPreferences userId="u1" />);
    await screen.findByText("Nudeln");

    const input = screen.getByLabelText("Ich mag: neuer Eintrag");
    fireEvent.change(input, { target: { value: "  Reis " } });
    fireEvent.submit(input.closest("form")!);

    await waitFor(() => expect(screen.getByText("Reis")).toBeInTheDocument());
    expect(puts).toEqual([{ likes: ["Nudeln", "Reis"], dislikes: ["Pilze"], allergies: ["Nüsse"] }]);
  });

  it("fügt Allergien über die eigene Eingabe hinzu", async () => {
    const puts = stubApi();
    render(<FoodPreferences userId="u1" />);
    await screen.findByText("Nudeln");

    const input = screen.getByLabelText("Allergie: neuer Eintrag");
    fireEvent.change(input, { target: { value: "Ei" } });
    fireEvent.click(screen.getByRole("button", { name: "Allergie hinzufügen" }));

    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toMatchObject({ allergies: ["Nüsse", "Ei"] });
  });

  it("entfernt Einträge über das Chip-x", async () => {
    const puts = stubApi();
    render(<FoodPreferences userId="u1" />);
    await screen.findByText("Pilze");

    fireEvent.click(screen.getByRole("button", { name: "Pilze entfernen" }));

    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toEqual({ likes: ["Nudeln"], dislikes: [], allergies: ["Nüsse"] });
    await waitFor(() => expect(screen.queryByText("Pilze")).not.toBeInTheDocument());
  });

  it("ignoriert Duplikate ohne Anfrage", async () => {
    const puts = stubApi();
    render(<FoodPreferences userId="u1" />);
    await screen.findByText("Nudeln");

    const input = screen.getByLabelText("Ich mag: neuer Eintrag");
    fireEvent.change(input, { target: { value: "nudeln" } });
    fireEvent.submit(input.closest("form")!);

    expect(puts).toHaveLength(0);
  });

  it("begrenzt die Länge eines Eintrags auf 80 Zeichen", async () => {
    stubApi();
    render(<FoodPreferences userId="u1" />);
    await screen.findByText("Nudeln");

    expect(screen.getByLabelText("Ich mag: neuer Eintrag")).toHaveAttribute("maxLength", "80");
  });

  it("sperrt das Hinzufügen bei 50 Einträgen", async () => {
    const many = Array.from({ length: 50 }, (_, i) => `Zutat ${i}`);
    stubApi({ likes: many, dislikes: [], allergies: [] });
    render(<FoodPreferences userId="u1" />);
    await screen.findByText("Zutat 0");

    expect(screen.getByLabelText("Ich mag: neuer Eintrag")).toBeDisabled();
    expect(screen.getByLabelText("Ich mag nicht: neuer Eintrag")).not.toBeDisabled();
  });

  it("zeigt einen Fehler, wenn das Speichern fehlschlägt, und behält den alten Stand", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string, options?: RequestInit) =>
        options?.method === "PUT"
          ? Promise.resolve({ ok: false, status: 500, statusText: "x", json: async () => ({ detail: "Serverfehler" }) })
          : Promise.resolve({ ok: true, status: 200, json: async () => ({ likes: ["Nudeln"], dislikes: [], allergies: [] }) })
      )
    );
    render(<FoodPreferences userId="u1" />);
    await screen.findByText("Nudeln");

    fireEvent.click(screen.getByRole("button", { name: "Nudeln entfernen" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Serverfehler");
    expect(screen.getByText("Nudeln")).toBeInTheDocument();
  });

  it("zeigt einen Ladefehler", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve({ ok: false, status: 403, statusText: "x", json: async () => ({ detail: "Kein Zugriff" }) }))
    );
    render(<FoodPreferences userId="u1" />);

    expect(await screen.findByText("Kein Zugriff")).toBeInTheDocument();
  });

  it("lädt im einklappbaren Modus erst nach dem Aufklappen", async () => {
    stubApi();
    render(<FoodPreferences userId="u1" name="Anna" collapsible />);

    expect(fetch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Das mag ich – Anna/ }));

    expect(await screen.findByText("Nudeln")).toBeInTheDocument();
  });
});
