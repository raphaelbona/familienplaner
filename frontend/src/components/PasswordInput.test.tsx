import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import PasswordInput from "./PasswordInput";

function Harness() {
  const [value, setValue] = useState("geheim123");
  return <PasswordInput id="pw" aria-label="Passwort" value={value} onChange={(e) => setValue(e.target.value)} />;
}

describe("PasswordInput", () => {
  it("versteckt das Passwort standardmässig", () => {
    render(<Harness />);

    expect(screen.getByLabelText("Passwort")).toHaveAttribute("type", "password");
    expect(screen.getByRole("button", { name: "Passwort anzeigen" })).toHaveAttribute("aria-pressed", "false");
  });

  it("zeigt und verbirgt das Passwort per Knopf, ohne den Wert zu ändern", () => {
    render(<Harness />);

    fireEvent.click(screen.getByRole("button", { name: "Passwort anzeigen" }));
    expect(screen.getByLabelText("Passwort")).toHaveAttribute("type", "text");
    expect(screen.getByLabelText("Passwort")).toHaveValue("geheim123");
    expect(screen.getByRole("button", { name: "Passwort verbergen" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "Passwort verbergen" }));
    expect(screen.getByLabelText("Passwort")).toHaveAttribute("type", "password");
  });

  it("löst kein Formular-Submit aus", () => {
    let submitted = false;
    render(
      <form onSubmit={(e) => { e.preventDefault(); submitted = true; }}>
        <PasswordInput aria-label="Passwort" defaultValue="x" />
      </form>
    );

    fireEvent.click(screen.getByRole("button", { name: "Passwort anzeigen" }));

    expect(submitted).toBe(false);
  });
});
