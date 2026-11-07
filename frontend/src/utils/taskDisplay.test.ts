import { describe, expect, it } from "vitest";
import { taskStatusHelp, taskStatusLabel, withoutExpired } from "./taskDisplay";

const template = { kind: "basis" as const, points_value: 0, bonus_multiplier: 1 };

describe("taskDisplay: verfallene Aufgaben", () => {
  it("'expired' heisst 'Verfallen'", () => {
    expect(taskStatusLabel("expired")).toBe("Verfallen");
    expect(taskStatusHelp("expired", template)).not.toBe("");
  });

  it("die anderen Status bleiben unverändert", () => {
    expect(taskStatusLabel("open")).toBe("Heute noch möglich");
    expect(taskStatusLabel("completed")).toBe("Wartet auf Eltern");
    expect(taskStatusLabel("approved")).toBe("Bestätigt");
  });

  it("withoutExpired entfernt nur verfallene Aufgaben", () => {
    const rows = [
      { id: "1", status: "open" as const },
      { id: "2", status: "expired" as const },
      { id: "3", status: "approved" as const },
    ];
    expect(withoutExpired(rows).map((r) => r.id)).toEqual(["1", "3"]);
  });
});
