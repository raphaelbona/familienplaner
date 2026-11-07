import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

// Mock fetch with endpoint awareness
vi.stubGlobal(
  "fetch",
  vi.fn((url: string) => {
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
    return Promise.reject(new Error("Unknown endpoint"));
  })
);

describe("App", () => {
  it("zeigt alle Navigationspunkte", async () => {
    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>
    );

    // Ohne Token zeigt die Startseite (Dashboard) den anonymen Fallback; abwarten,
    // damit der State-Update aus dem useEffect nicht nach Testende passiert.
    await waitFor(() => {
      expect(screen.getByText(/Nächste Schritte/i)).toBeInTheDocument();
    });

    expect(screen.getByRole("link", { name: /heute/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /aufgaben/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /belohnungen/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /essen/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /mehr/i })).toBeInTheDocument();
  });
});
