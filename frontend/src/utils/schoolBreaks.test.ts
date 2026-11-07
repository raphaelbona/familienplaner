import { describe, expect, it } from "vitest";
import type { SchoolBreak } from "../api/schoolBreaks";
import { formatBreakRange, sortBreaks } from "./schoolBreaks";

function brk(id: string, start: string, end: string): SchoolBreak {
  return { id, title: id, kind: "ferien", start_date: start, end_date: end, child_user_ids: [], source: "manual" };
}

describe("formatBreakRange", () => {
  it("shows a single day as a full date", () => {
    expect(formatBreakRange("2026-10-14", "2026-10-14")).toBe("14.10.2026");
  });

  it("shows a range as dd.mm. – dd.mm.yyyy", () => {
    expect(formatBreakRange("2026-10-12", "2026-10-23")).toBe("12.10. – 23.10.2026");
  });

  it("shows both years when the range crosses New Year", () => {
    expect(formatBreakRange("2026-12-21", "2027-01-01")).toBe("21.12.2026 – 01.01.2027");
  });
});

describe("sortBreaks", () => {
  it("puts running and upcoming breaks first by start, then past ones, most recent first", () => {
    const list = [
      brk("alt1", "2026-07-01", "2026-08-10"),
      brk("spaeter", "2026-12-20", "2027-01-03"),
      brk("alt2", "2026-09-01", "2026-09-05"),
      brk("laufend", "2026-10-05", "2026-10-08"),
      brk("bald", "2026-10-12", "2026-10-23"),
    ];

    const { upcoming, past } = sortBreaks(list, "2026-10-08");

    expect(upcoming.map((b) => b.id)).toEqual(["laufend", "bald", "spaeter"]);
    expect(past.map((b) => b.id)).toEqual(["alt2", "alt1"]);
  });

  it("treats a break that ends today as still running", () => {
    expect(sortBreaks([brk("heute", "2026-10-06", "2026-10-08")], "2026-10-08").upcoming).toHaveLength(1);
  });
});
