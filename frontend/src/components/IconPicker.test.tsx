import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ICON_GROUPS, TASK_REWARD_ICONS } from "../constants/taskRewardIcons";
import IconPicker from "./IconPicker";

describe("Symbolliste", () => {
  it("bietet deutlich mehr als die früheren 24 Symbole", () => {
    expect(TASK_REWARD_ICONS.length).toBeGreaterThan(120);
  });

  it("enthält keine doppelten Symbole", () => {
    const all = ICON_GROUPS.flatMap((g) => g.icons.map((i) => i.e));
    expect(new Set(all).size).toBe(all.length);
  });

  it("jedes Symbol passt in die Datenbankspalte (8 Zeichen)", () => {
    for (const icon of TASK_REWARD_ICONS) {
      expect([...icon].length).toBeLessThanOrEqual(8);
    }
  });

  it("hat ein Symbol für Essensbox ausräumen und für Abwaschen", () => {
    const find = (word: string) =>
      ICON_GROUPS.flatMap((g) => g.icons).filter((i) => i.k.toLowerCase().includes(word));
    expect(find("essensbox").map((i) => i.e)).toContain("🍱");
    expect(find("abwaschen").map((i) => i.e)).toEqual(expect.arrayContaining(["🍽️", "🧼", "🧽"]));
  });
});

describe("IconPicker", () => {
  it("zeigt alle Kategorien und wählt ein Symbol", () => {
    const onChange = vi.fn();
    render(<IconPicker value={null} onChange={onChange} />);

    expect(screen.getByRole("region", { name: "Küche und Abwasch" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Spiel, Freizeit und Belohnung" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Essensbox Znünibox" }));
    expect(onChange).toHaveBeenCalledWith("🍱");
  });

  it("findet Symbole per Suche, auch ohne Umlaute und Gross-/Kleinschreibung", () => {
    render(<IconPicker value={null} onChange={() => {}} />);

    fireEvent.change(screen.getByLabelText("Symbol suchen"), { target: { value: "ZNUNIBOX" } });
    expect(screen.getByRole("button", { name: "Essensbox Znünibox" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Besen/ })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Symbol suchen"), { target: { value: "abwaschen" } });
    expect(screen.getByRole("button", { name: /Seife abwaschen/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Schwamm abwaschen/ })).toBeInTheDocument();
  });

  it("mehrere Suchwörter müssen alle passen", () => {
    render(<IconPicker value={null} onChange={() => {}} />);

    fireEvent.change(screen.getByLabelText("Symbol suchen"), { target: { value: "seife waschen" } });
    expect(screen.getByRole("button", { name: /Seife/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Schwamm/ })).not.toBeInTheDocument();
  });

  it("findet Umlaute auch mit ae/oe/ue und umgekehrt", () => {
    render(<IconPicker value={null} onChange={() => {}} />);
    const search = screen.getByLabelText("Symbol suchen");

    fireEvent.change(search, { target: { value: "haende" } });
    expect(screen.getByRole("button", { name: /Seife/ })).toBeInTheDocument();

    fireEvent.change(search, { target: { value: "hände" } });
    expect(screen.getByRole("button", { name: /Seife/ })).toBeInTheDocument();

    fireEvent.change(search, { target: { value: "guetzli" } });
    expect(screen.getByRole("button", { name: /Guetzli/ })).toBeInTheDocument();
  });

  it("sagt Bescheid, wenn nichts gefunden wird", () => {
    render(<IconPicker value={null} onChange={() => {}} />);

    fireEvent.change(screen.getByLabelText("Symbol suchen"), { target: { value: "xyzxyz" } });
    expect(screen.getByText("Kein Symbol gefunden.")).toBeInTheDocument();
  });

  it("markiert das gewählte Symbol", () => {
    render(<IconPicker value="🍱" onChange={() => {}} />);

    expect(screen.getByRole("button", { name: "Essensbox Znünibox" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Besen wischen" })).toHaveAttribute("aria-pressed", "false");
  });

  it("Enter in der Suche schickt das umgebende Formular nicht ab", () => {
    const onSubmit = vi.fn((e: { preventDefault: () => void }) => e.preventDefault());
    render(
      <form onSubmit={onSubmit}>
        <IconPicker value={null} onChange={() => {}} />
      </form>
    );

    const search = screen.getByLabelText("Symbol suchen");
    const allowed = fireEvent.keyDown(search, { key: "Enter" });

    expect(allowed).toBe(false);
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
