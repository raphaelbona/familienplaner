import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { setDeviceToken } from "../api/client";
import Login from "./Login";

function renderLogin() {
  return render(
    <MemoryRouter>
      <Login />
    </MemoryRouter>
  );
}

afterEach(() => {
  localStorage.clear();
});

describe("Login", () => {
  it("zeigt auf einem nicht freigegebenen Gerät nur den Eltern-Login", () => {
    renderLogin();

    expect(screen.getByText("Für Eltern.")).toBeInTheDocument();
    expect(screen.queryByText(/Kinder/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Wand-Dashboard/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Kind oder Wand-Dashboard/ })).not.toBeInTheDocument();
  });

  it("verweist auf einem freigegebenen Gerät auf die PIN-Anmeldung", () => {
    setDeviceToken("device-token", "device-1");
    renderLogin();

    expect(screen.getByText(/Kinder und Wand-Dashboard melden sich mit PIN an/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Kind oder Wand-Dashboard/ })).toHaveAttribute("href", "/kind-login");
  });
});
