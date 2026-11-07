import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import TaskTile, { tileRewardBadge, type TileTask } from "./TaskTile";

const base: TileTask = { id: "t1", title: "Teller wegräumen", status: "open", icon: "🍽️" };

describe("TaskTile", () => {
  it("zeigt das grosse Symbol und den Titel, offen = nicht gedrückt", () => {
    render(<TaskTile task={base} onToggle={() => {}} />);

    const tile = screen.getByRole("button", { name: "Teller wegräumen, offen, antippen wenn erledigt" });
    expect(tile).toHaveAttribute("aria-pressed", "false");
    expect(tile).toBeEnabled();
    expect(tile).toHaveClass("task-tile--open");
    expect(screen.getByText("🍽️")).toBeInTheDocument();
    expect(screen.getByText("Teller wegräumen")).toBeInTheDocument();
  });

  it("fällt ohne Symbol auf den Stern zurück", () => {
    render(<TaskTile task={{ ...base, icon: null }} onToggle={() => {}} />);

    expect(screen.getByText(/⭐/)).toBeInTheDocument();
  });

  it("erledigt = gedrückt, grüner Haken, Beschriftung zum Zurücknehmen", () => {
    render(<TaskTile task={{ ...base, status: "completed" }} onToggle={() => {}} />);

    const tile = screen.getByRole("button", { name: "Teller wegräumen, erledigt, antippen zum Zurücknehmen" });
    expect(tile).toHaveAttribute("aria-pressed", "true");
    expect(tile).toBeEnabled();
    expect(tile).toHaveClass("task-tile--completed");
    expect(screen.getByText("✓")).toBeInTheDocument();
  });

  it("bestätigt = gesperrt mit Schloss", () => {
    const onToggle = vi.fn();
    render(<TaskTile task={{ ...base, status: "approved" }} onToggle={onToggle} />);

    const tile = screen.getByRole("button", { name: "Teller wegräumen, von Eltern bestätigt" });
    expect(tile).toBeDisabled();
    expect(tile).toHaveClass("task-tile--approved");
    expect(screen.getByText("🔒")).toBeInTheDocument();

    fireEvent.click(tile);
    expect(onToggle).not.toHaveBeenCalled();
  });

  it("ruft onToggle mit id und Status bei offener und erledigter Kachel auf", () => {
    const onToggle = vi.fn();
    const { rerender } = render(<TaskTile task={base} onToggle={onToggle} />);

    fireEvent.click(screen.getByRole("button"));
    expect(onToggle).toHaveBeenLastCalledWith("t1", "open");

    rerender(<TaskTile task={{ ...base, status: "completed" }} onToggle={onToggle} />);
    fireEvent.click(screen.getByRole("button"));
    expect(onToggle).toHaveBeenLastCalledWith("t1", "completed");
    expect(onToggle).toHaveBeenCalledTimes(2);
  });

  it("ist während der Anfrage gesperrt und meldet busy", () => {
    const onToggle = vi.fn();
    render(<TaskTile task={base} onToggle={onToggle} busy />);

    const tile = screen.getByRole("button");
    expect(tile).toBeDisabled();
    expect(tile).toHaveAttribute("aria-busy", "true");
    expect(tile).toHaveClass("task-tile--busy");
    fireEvent.click(tile);
    expect(onToggle).not.toHaveBeenCalled();
  });

  it("zeigt die kleine Belohnung nur bei Bonus- und Geld-Aufgaben", () => {
    const { rerender } = render(
      <TaskTile
        task={{ ...base, template: { kind: "bonus", points_value: 5, bonus_multiplier: 2, money_value_chf: 0 } }}
        onToggle={() => {}}
      />
    );
    expect(screen.getByText("+10")).toBeInTheDocument();

    rerender(
      <TaskTile
        task={{ ...base, template: { kind: "geld", points_value: 0, bonus_multiplier: 1, money_value_chf: 2 } }}
        onToggle={() => {}}
      />
    );
    expect(screen.getByText("CHF 2")).toBeInTheDocument();

    rerender(
      <TaskTile
        task={{ ...base, template: { kind: "basis", points_value: 0, bonus_multiplier: 1, money_value_chf: 0 } }}
        onToggle={() => {}}
      />
    );
    expect(screen.queryByText(/CHF|\+\d/)).not.toBeInTheDocument();
  });

  it("formatiert Geldbeträge mit Rappen", () => {
    expect(
      tileRewardBadge({
        ...base,
        template: { kind: "geld", points_value: 0, bonus_multiplier: 1, money_value_chf: 1.5 },
      })
    ).toBe("CHF 1.50");
  });

  it("noch nicht begonnene Phase: gedimmt, gesperrt, mit 'ab 19:00' und ohne Auslösung", () => {
    const onToggle = vi.fn();
    render(<TaskTile task={base} onToggle={onToggle} notBefore="ab 19:00" />);

    const tile = screen.getByRole("button", { name: "Teller wegräumen, noch nicht dran, ab 19:00" });
    expect(tile).toBeDisabled();
    expect(tile).toHaveClass("task-tile--upcoming");
    expect(screen.getByText("ab 19:00")).toBeInTheDocument();

    fireEvent.click(tile);
    expect(onToggle).not.toHaveBeenCalled();
  });

  it("eine schon erledigte Kachel bleibt auch in einer späteren Phase zurücknehmbar", () => {
    const onToggle = vi.fn();
    render(<TaskTile task={{ ...base, status: "completed" }} onToggle={onToggle} notBefore="ab 19:00" />);

    const tile = screen.getByRole("button", { name: "Teller wegräumen, erledigt, antippen zum Zurücknehmen" });
    expect(tile).toBeEnabled();
    expect(screen.queryByText("ab 19:00")).toBeNull();
    fireEvent.click(tile);
    expect(onToggle).toHaveBeenCalledWith("t1", "completed");
  });

  it("verfallen = gesperrt und grau, stürzt nicht ab", () => {
    const onToggle = vi.fn();
    render(<TaskTile task={{ ...base, status: "expired" }} onToggle={onToggle} />);

    const tile = screen.getByRole("button", { name: "Teller wegräumen, verfallen" });
    expect(tile).toBeDisabled();
    expect(tile).toHaveClass("task-tile--expired");
    fireEvent.click(tile);
    expect(onToggle).not.toHaveBeenCalled();
  });
});
