import { describe, expect, it } from "vitest";
import type { DayPhase } from "../api/dayPhases";
import {
  formatHhmm,
  groupByPhase,
  notBeforeLabel,
  parseHhmm,
  phaseRangeLabel,
  phaseStartLabel,
  phaseState,
  sortPhases,
} from "./dayPhases";

function phase(id: string, start: string, end: string, extra: Partial<DayPhase> = {}): DayPhase {
  return { id, name: id, icon: null, start_time: start, end_time: end, day_type: "both", sort_order: 0, ...extra };
}

const MORGEN = phase("morgen", "06:00:00", "08:00:00");
const NACHMITTAG = phase("nachmittag", "15:00:00", "18:00:00");
const ABEND = phase("abend", "19:00:00", "21:00:00");

function at(hours: number, minutes = 0): Date {
  return new Date(2026, 9, 30, hours, minutes);
}

describe("parseHhmm / formatHhmm", () => {
  it("liest HH:MM und HH:MM:SS als Minuten seit Mitternacht", () => {
    expect(parseHhmm("06:00:00")).toBe(360);
    expect(parseHhmm("19:30")).toBe(19 * 60 + 30);
    expect(parseHhmm("0:05")).toBe(5);
    expect(parseHhmm("23:59:59")).toBe(23 * 60 + 59);
  });

  it("lehnt ungültige Zeiten ab", () => {
    expect(parseHhmm("")).toBeNull();
    expect(parseHhmm(null)).toBeNull();
    expect(parseHhmm("24:00")).toBeNull();
    expect(parseHhmm("12:60")).toBeNull();
    expect(parseHhmm("abc")).toBeNull();
  });

  it("formatiert auf HH:MM", () => {
    expect(formatHhmm("07:00:00")).toBe("07:00");
    expect(formatHhmm("7:05")).toBe("07:05");
    expect(formatHhmm("kaputt")).toBe("kaputt");
  });
});

describe("Beschriftungen", () => {
  it("'ab 07:00' und '06:00–08:00'", () => {
    expect(phaseStartLabel(phase("x", "07:00:00", "08:00:00"))).toBe("ab 07:00");
    expect(phaseRangeLabel(MORGEN)).toBe("06:00–08:00");
  });
});

describe("phaseState", () => {
  it("vor dem Beginn = kommt noch, ab Beginn bis Ende (exklusiv) = jetzt, danach = vorbei", () => {
    expect(phaseState(ABEND, at(18, 59))).toBe("upcoming");
    expect(phaseState(ABEND, at(19, 0))).toBe("current");
    expect(phaseState(ABEND, at(20, 59))).toBe("current");
    expect(phaseState(ABEND, at(21, 0))).toBe("past");
  });

  it("ein Ende um oder vor dem Beginn (z. B. bis 00:00) läuft bis Tagesende", () => {
    const night = phase("nacht", "20:00:00", "00:00:00");
    expect(phaseState(night, at(23, 59))).toBe("current");
    expect(phaseState(night, at(19, 59))).toBe("upcoming");
  });

  it("unlesbare Zeiten sperren nie", () => {
    expect(phaseState(phase("kaputt", "??", "!!"), at(3))).toBe("current");
  });
});

describe("sortPhases", () => {
  it("sortiert nach Beginn, dann Reihenfolge, dann Name", () => {
    const a = phase("a", "07:00:00", "08:00:00", { sort_order: 2 });
    const b = phase("b", "07:00:00", "08:00:00", { sort_order: 1 });
    const sorted = sortPhases([ABEND, a, b, MORGEN]);
    expect(sorted.map((p) => p.id)).toEqual(["morgen", "b", "a", "abend"]);
  });

  it("verändert die Eingabe nicht", () => {
    const input = [ABEND, MORGEN];
    sortPhases(input);
    expect(input.map((p) => p.id)).toEqual(["abend", "morgen"]);
  });
});

describe("groupByPhase", () => {
  type Item = { id: string; phase_id: string | null };

  it("ordnet ohne Phase zuerst, dann die Phasen nach Beginn; Reihenfolge innerhalb bleibt", () => {
    const items: Item[] = [
      { id: "1", phase_id: "abend" },
      { id: "2", phase_id: null },
      { id: "3", phase_id: "morgen" },
      { id: "4", phase_id: "abend" },
      { id: "5", phase_id: null },
    ];
    const groups = groupByPhase(items, [ABEND, MORGEN], at(7));
    expect(groups.map((g) => g.phase?.id ?? null)).toEqual([null, "morgen", "abend"]);
    expect(groups.map((g) => g.items.map((i) => i.id))).toEqual([["2", "5"], ["3"], ["1", "4"]]);
    expect(groups.map((g) => g.state)).toEqual([null, "current", "upcoming"]);
  });

  it("lässt leere Phasen weg", () => {
    const groups = groupByPhase([{ id: "1", phase_id: "morgen" }], [MORGEN, NACHMITTAG, ABEND], at(7));
    expect(groups).toHaveLength(1);
  });

  it("behandelt unbekannte Phasen (gelöscht oder nicht geladen) wie ohne Phase", () => {
    const groups = groupByPhase([{ id: "1", phase_id: "weg" }, { id: "2" }], [], at(7));
    expect(groups).toHaveLength(1);
    expect(groups[0].phase).toBeNull();
    expect(groups[0].items.map((i) => i.id)).toEqual(["1", "2"]);
  });

  it("liefert ohne Einträge keine Gruppen", () => {
    expect(groupByPhase([], [MORGEN], at(7))).toEqual([]);
  });
});

describe("notBeforeLabel", () => {
  it("nur für Phasen, die heute noch nicht begonnen haben", () => {
    expect(notBeforeLabel({ phase_id: "abend" }, [ABEND], at(10))).toBe("ab 19:00");
    expect(notBeforeLabel({ phase_id: "abend" }, [ABEND], at(19))).toBeNull();
    expect(notBeforeLabel({ phase_id: "abend" }, [ABEND], at(22))).toBeNull();
  });

  it("ohne oder mit unbekannter Phase gibt es keine Sperre", () => {
    expect(notBeforeLabel({ phase_id: null }, [ABEND], at(1))).toBeNull();
    expect(notBeforeLabel({}, [ABEND], at(1))).toBeNull();
    expect(notBeforeLabel({ phase_id: "weg" }, [ABEND], at(1))).toBeNull();
  });
});
