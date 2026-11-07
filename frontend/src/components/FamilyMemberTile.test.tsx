import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import FamilyMemberTile from "./FamilyMemberTile";

const days = ["done", "open", "none", "none", "none", "none", "none"] as const;

function renderTile(overrides: Partial<React.ComponentProps<typeof FamilyMemberTile>> = {}) {
  const onToggleTask = vi.fn();
  render(
    <FamilyMemberTile
      avatar="🦄"
      name="Mia"
      color="#e2793d"
      todayDone={1}
      todayTotal={3}
      todayTasks={[
        { id: "a", title: "Zimmer aufräumen", status: "open", icon: "🧹" },
        { id: "b", title: "Tisch decken", status: "completed", icon: "🍽️" },
        { id: "c", title: "Müll rausbringen", status: "approved", icon: null },
      ]}
      weekDays={[...days]}
      todayIndex={0}
      onToggleTask={onToggleTask}
      completingTaskId={null}
      {...overrides}
    />
  );
  return { onToggleTask };
}

describe("FamilyMemberTile", () => {
  it("zeigt Name, kurzen Fortschritt und eine Kachel pro Aufgabe statt langer Sätze", () => {
    renderTile();

    expect(screen.getByText("Mia")).toBeInTheDocument();
    expect(screen.getByText("1/3")).toBeInTheDocument();
    expect(screen.queryByText(/erledigt$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Woche:/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Zimmer aufräumen, offen/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Tisch decken, erledigt/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Müll rausbringen, von Eltern bestätigt/ })).toBeDisabled();
    expect(document.querySelector(".task-tile-grid")).not.toBeNull();
  });

  it("ruft onToggleTask mit Id und Status auf (erledigen und zurücknehmen)", () => {
    const { onToggleTask } = renderTile();

    fireEvent.click(screen.getByRole("button", { name: /Zimmer aufräumen/ }));
    expect(onToggleTask).toHaveBeenLastCalledWith("a", "open");

    fireEvent.click(screen.getByRole("button", { name: /Tisch decken/ }));
    expect(onToggleTask).toHaveBeenLastCalledWith("b", "completed");
  });

  it("sperrt nur die Kachel, deren Anfrage läuft", () => {
    renderTile({ completingTaskId: "a" });

    expect(screen.getByRole("button", { name: /Zimmer aufräumen/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Tisch decken/ })).toBeEnabled();
  });

  it("zeigt 'Heute frei' ohne Aufgaben und keinen Fortschritt", () => {
    renderTile({ todayTasks: [], todayTotal: 0, todayDone: 0 });

    expect(screen.getByText(/Heute frei/)).toBeInTheDocument();
    expect(document.querySelector(".task-tile-grid")).toBeNull();
    expect(screen.queryByText(/\d\/\d/)).not.toBeInTheDocument();
  });

  it("öffnet die Kind-Ansicht über den Kopfbereich", () => {
    const onSelect = vi.fn();
    renderTile({ onSelect });

    fireEvent.click(screen.getByRole("button", { name: /Mia/ }));
    expect(onSelect).toHaveBeenCalledTimes(1);
  });
});
