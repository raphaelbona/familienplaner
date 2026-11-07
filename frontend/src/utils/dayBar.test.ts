import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  blockGeometry,
  breaksForChild,
  describeDay,
  eventOverlapsDay,
  formatInMinutes,
  formatMinutes,
  formatShortDate,
  layoutLabels,
  holidayForDay,
  summarizeDay,
  type DayBreak,
  type DayEvent,
  type DayLesson,
} from "./dayBar";

beforeEach(() => {
  vi.stubEnv("TZ", "Europe/Zurich");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

// Thursday 8 October 2026 -- everything is built from LOCAL date parts so the tests do not depend on the TZ.
const TODAY = "2026-10-08";
const TOMORROW = "2026-10-09";

function at(hours: number, minutes = 0, day = 8): Date {
  return new Date(2026, 9, day, hours, minutes);
}

function lesson(start: string, end: string): DayLesson {
  return { start_time: start, end_time: end };
}

function event(id: string, title: string, start: Date, end: Date, allDay = false): DayEvent {
  return { id, title, start: start.toISOString(), end: end.toISOString(), all_day: allDay };
}

describe("summarizeDay: school blocks", () => {
  it("collapses a morning of lessons into one block from school start to school end", () => {
    const s = summarizeDay({
      lessons: [lesson("08:20", "09:05"), lesson("09:05", "09:50"), lesson("10:10", "10:55"), lesson("11:05", "11:50")],
      events: [],
      date: TOMORROW,
      now: at(10),
    });
    expect(s.blocks).toEqual([{ start: 8 * 60 + 20, end: 11 * 60 + 50, kind: "school", done: false }]);
    expect(s.state).toBe("active");
  });

  it.each([
    ["gap of 0 minutes", "09:00", "09:00", 1],
    ["gap of exactly 20 minutes still merges", "09:00", "09:20", 1],
    ["gap of 21 minutes starts a new block", "09:00", "09:21", 2],
    ["a lunch break splits morning and afternoon", "11:50", "13:30", 2],
  ])("%s", (_name, firstEnd, secondStart, expectedBlocks) => {
    const s = summarizeDay({
      lessons: [lesson("08:00", firstEnd), lesson(secondStart, "15:00")],
      events: [],
      date: TOMORROW,
      now: at(7),
    });
    expect(s.blocks).toHaveLength(expectedBlocks);
  });

  it("sorts unordered lessons, merges overlapping ones and ignores broken entries", () => {
    const s = summarizeDay({
      lessons: [lesson("10:00", "11:00"), lesson("08:00", "10:30"), lesson("abc", "12:00"), lesson("12:00", "12:00"), lesson("14:00", "13:00")],
      events: [],
      date: TOMORROW,
      now: at(7),
    });
    expect(s.blocks.map((b) => [b.start, b.end])).toEqual([[480, 660]]);
  });

  it("accepts HH:MM:SS times", () => {
    const s = summarizeDay({ lessons: [lesson("08:20:00", "09:05:00")], events: [], date: TOMORROW, now: at(7) });
    expect(s.blocks).toHaveLength(1);
    expect(s.blocks[0].start).toBe(500);
  });
});

describe("summarizeDay: tags", () => {
  it.each([
    ["07:44 is an early start", "07:44", ["early"]],
    ["07:45 is not early", "07:45", []],
    ["08:20 is a normal start", "08:20", []],
    ["06:50 is early", "06:50", ["early"]],
  ])("%s", (_name, start, tags) => {
    const s = summarizeDay({ lessons: [lesson(start, "09:30")], events: [], date: TOMORROW, now: at(7) });
    expect(s.tags).toEqual(tags);
  });

  it.each([
    ["12:29 does not count", "12:29", []],
    ["12:30 counts", "12:30", ["afternoon"]],
    ["13:30 counts", "13:30", ["afternoon"]],
  ])("afternoon: %s", (_name, start, tags) => {
    const s = summarizeDay({ lessons: [lesson("08:20", "11:50"), lesson(start, "15:10")], events: [], date: TOMORROW, now: at(7) });
    expect(s.tags).toEqual(tags);
  });

  it("gives both tags (early first) when the day has both", () => {
    const s = summarizeDay({ lessons: [lesson("07:30", "10:00"), lesson("13:30", "15:00")], events: [], date: TOMORROW, now: at(7) });
    expect(s.tags).toEqual(["early", "afternoon"]);
  });

  it("has no tags without lessons", () => {
    const s = summarizeDay({ lessons: [], events: [], date: TOMORROW, now: at(7) });
    expect(s.tags).toEqual([]);
  });
});

describe("summarizeDay: states and done blocks (injected now)", () => {
  const lessons = [lesson("08:20", "11:50"), lesson("13:30", "15:10")];

  it("marks only the finished block as done during the day", () => {
    const s = summarizeDay({ lessons, events: [], date: TODAY, now: at(12, 0) });
    expect(s.blocks.map((b) => b.done)).toEqual([true, false]);
    expect(s.state).toBe("active");
  });

  it("a running block is not done", () => {
    const s = summarizeDay({ lessons, events: [], date: TODAY, now: at(10, 0) });
    expect(s.blocks.map((b) => b.done)).toEqual([false, false]);
  });

  it("a block is done at its exact end minute", () => {
    const s = summarizeDay({ lessons, events: [], date: TODAY, now: at(11, 50) });
    expect(s.blocks[0].done).toBe(true);
  });

  it("is 'done' once every block of today is over", () => {
    const s = summarizeDay({ lessons, events: [], date: TODAY, now: at(16, 0) });
    expect(s.blocks.every((b) => b.done)).toBe(true);
    expect(s.state).toBe("done");
    expect(s.next).toBeNull();
  });

  it("is 'done' when the last event is over too, but not while an event is still ahead", () => {
    const e = event("e1", "Arzt", at(16, 30), at(17, 15));
    expect(summarizeDay({ lessons, events: [e], date: TODAY, now: at(16, 0) }).state).toBe("active");
    expect(summarizeDay({ lessons, events: [e], date: TODAY, now: at(17, 30) }).state).toBe("done");
  });

  it("tomorrow is never done, whatever the clock says", () => {
    const s = summarizeDay({ lessons, events: [], date: TOMORROW, now: at(23, 0) });
    expect(s.blocks.every((b) => !b.done)).toBe(true);
    expect(s.state).toBe("active");
    expect(s.next).toBeNull();
  });

  it("is 'free' without lessons and events", () => {
    expect(summarizeDay({ lessons: [], events: [], date: TODAY, now: at(9) }).state).toBe("free");
  });

  it("an all-day event keeps the day active and is never over", () => {
    const e = event("a1", "Schulreise", at(0), at(0, 0, 9), true);
    const s = summarizeDay({ lessons: [], events: [e], date: TODAY, now: at(20) });
    expect(s.state).toBe("active");
    expect(s.chips).toHaveLength(1);
  });
});

describe("summarizeDay: events and chips", () => {
  it("lists only non-school timed events of the day as chips, with 'HH:MM' times", () => {
    const events = [
      event("e1", "Kita", at(8, 0), at(12, 0)),
      event("e2", "Zahnarzt", at(15, 30), at(16, 0)),
      event("x", "Morgen-Termin", at(9, 0, 9), at(10, 0, 9)),
    ];
    const s = summarizeDay({ lessons: [], events, date: TODAY, now: at(7, 0) });
    expect(s.blocks).toEqual([]);
    expect(s.chips.map((c) => [c.title, c.time, c.allDay])).toEqual([
      ["Kita", "08:00", false],
      ["Zahnarzt", "15:30", false],
    ]);
    expect(s.state).toBe("active");
  });

  it("works with events only (no lessons) and with lessons only (no chips)", () => {
    expect(summarizeDay({ lessons: [], events: [event("e", "A", at(9), at(10))], date: TODAY, now: at(7) }).blocks).toEqual([]);
    expect(summarizeDay({ lessons: [lesson("08:00", "09:00")], events: [], date: TODAY, now: at(7) }).chips).toEqual([]);
  });

  it("drops events that are already over today, keeps running and later ones", () => {
    const events = [event("a", "Vorbei", at(8), at(9)), event("b", "Läuft", at(9), at(11)), event("c", "Später", at(14), at(15))];
    const s = summarizeDay({ lessons: [], events, date: TODAY, now: at(10) });
    expect(s.chips.map((c) => c.title)).toEqual(["Läuft", "Später"]);
  });

  it("treats an instant event (start == end) as running for 15 minutes", () => {
    const e = event("a", "Anruf", at(10), at(10));
    expect(summarizeDay({ lessons: [], events: [e], date: TODAY, now: at(10, 10) }).chips).toHaveLength(1);
    expect(summarizeDay({ lessons: [], events: [e], date: TODAY, now: at(10, 15) }).chips).toHaveLength(0);
  });

  it("puts all-day chips (no time) before timed ones", () => {
    const events = [event("t", "Arzt", at(9), at(10)), event("d", "Geburtstag", at(0), at(0, 0, 9), true)];
    const s = summarizeDay({ lessons: [], events, date: TODAY, now: at(7) });
    expect(s.chips.map((c) => [c.title, c.time, c.allDay])).toEqual([
      ["Geburtstag", null, true],
      ["Arzt", "09:00", false],
    ]);
  });

  it("shows a multi-day all-day event on every covered day but not after its exclusive end", () => {
    const camp = event("camp", "Lager", at(0, 0, 8), at(0, 0, 10), true); // 8th and 9th
    expect(summarizeDay({ lessons: [], events: [camp], date: TODAY, now: at(7) }).chips).toHaveLength(1);
    expect(summarizeDay({ lessons: [], events: [camp], date: TOMORROW, now: at(7) }).chips).toHaveLength(1);
    expect(summarizeDay({ lessons: [], events: [camp], date: "2026-10-10", now: at(7) }).chips).toHaveLength(0);
  });

  it("anchors all-day events stored at UTC midnight on the right local day", () => {
    // Worker-synced all-day rows are 00:00 UTC (= 01:00/02:00 local), end exclusive the next UTC midnight.
    const e: DayEvent = { id: "u", title: "Feiertag", start: "2026-10-08T00:00:00Z", end: "2026-10-09T00:00:00Z", all_day: true };
    expect(summarizeDay({ lessons: [], events: [e], date: TODAY, now: at(7) }).chips).toHaveLength(1);
    expect(summarizeDay({ lessons: [], events: [e], date: TOMORROW, now: at(7) }).chips).toHaveLength(0);
  });

  it("shows an overnight event on both days; the continuing day gets a time-less chip", () => {
    const night = event("n", "Übernachten", at(20), at(8, 0, 9));
    const today = summarizeDay({ lessons: [], events: [night], date: TODAY, now: at(7) });
    const tomorrow = summarizeDay({ lessons: [], events: [night], date: TOMORROW, now: at(7) });
    expect(today.chips[0]).toMatchObject({ time: "20:00", allDay: false });
    expect(tomorrow.chips[0]).toMatchObject({ time: null, allDay: true });
  });

  it("ignores events with unparseable dates", () => {
    const broken: DayEvent = { id: "b", title: "Kaputt", start: "nope", end: "nope", all_day: false };
    expect(summarizeDay({ lessons: [], events: [broken], date: TODAY, now: at(7) }).state).toBe("free");
  });
});

describe("summarizeDay: next item and hint", () => {
  it("points at the next school block with a minute hint", () => {
    const s = summarizeDay({ lessons: [lesson("08:20", "11:50")], events: [], date: TODAY, now: at(7, 55) });
    expect(s.next).toEqual({ type: "block", index: 0, startMin: 500, hint: "in 25 Min" });
  });

  it("points at an event when it starts before the next school block", () => {
    const s = summarizeDay({
      lessons: [lesson("13:30", "15:00")],
      events: [event("k", "Kita", at(8, 0), at(12, 0))],
      date: TODAY,
      now: at(7, 0),
    });
    expect(s.next).toEqual({ type: "chip", id: "k", startMin: 480, hint: "in 1 Std" });
  });

  it("does not hint a running block but the following one", () => {
    const s = summarizeDay({ lessons: [lesson("08:00", "11:00"), lesson("13:30", "15:00")], events: [], date: TODAY, now: at(9) });
    expect(s.next).toMatchObject({ type: "block", index: 1 });
  });

  it("has no next item when everything is behind us or on another day", () => {
    expect(summarizeDay({ lessons: [lesson("08:00", "09:00")], events: [], date: TODAY, now: at(10) }).next).toBeNull();
    expect(summarizeDay({ lessons: [lesson("08:00", "09:00")], events: [], date: TOMORROW, now: at(7) }).next).toBeNull();
  });

  it.each([
    [0, "jetzt"],
    [1, "in 1 Min"],
    [25, "in 25 Min"],
    [59, "in 59 Min"],
    [60, "in 1 Std"],
    [90, "in 1 Std 30 Min"],
    [120, "in 2 Std"],
  ])("formats %i minutes as '%s'", (minutes, text) => {
    expect(formatInMinutes(minutes)).toBe(text);
  });
});

describe("blockGeometry (track 07:00-17:00)", () => {
  const geo = (start: string, end: string) => {
    const s = summarizeDay({ lessons: [lesson(start, end)], events: [], date: TOMORROW, now: at(7) });
    return blockGeometry(s.blocks[0]);
  };

  it("places a block at its true position and width", () => {
    expect(geo("08:20", "11:50")).toMatchObject({ left: 13.33, width: 35 });
  });

  it("clamps a block that starts before 07:00 to the left edge", () => {
    expect(geo("06:30", "09:00")).toMatchObject({ left: 0, width: 20 });
  });

  it("clamps a block that ends after 17:00 to the right edge", () => {
    expect(geo("15:00", "18:30")).toMatchObject({ left: 80, width: 20 });
  });

  it("keeps a visible stub for a block entirely outside the track", () => {
    const late = geo("23:00", "23:59");
    expect(late.left + late.width).toBe(100);
    expect(late.width).toBeGreaterThan(0);
    const early = geo("05:00", "06:00");
    expect(early.left).toBe(0);
    expect(early.width).toBeGreaterThan(0);
  });

  it("never lets the label times be clamped (they are the real times)", () => {
    const s = summarizeDay({ lessons: [lesson("06:30", "18:30")], events: [], date: TOMORROW, now: at(7) });
    expect(formatMinutes(s.blocks[0].start)).toBe("06:30");
    expect(formatMinutes(s.blocks[0].end)).toBe("18:30");
  });
});

describe("layoutLabels", () => {
  it("keeps labels in one row when they have room", () => {
    const rows = layoutLabels([
      { left: 13, width: 35 },
      { left: 55, width: 16 },
    ], 40);
    expect(rows.map((r) => r.row)).toEqual([0, 0]);
    expect(rows.map((r) => r.align)).toEqual(["left", "left"]);
  });

  it("moves an overlapping label to a second row", () => {
    const rows = layoutLabels([
      { left: 10, width: 10 },
      { left: 30, width: 10 },
    ], 40);
    expect(rows.map((r) => r.row)).toEqual([0, 1]);
  });

  it("right-aligns a label that would run out of the track", () => {
    const rows = layoutLabels([{ left: 90, width: 10 }], 40);
    expect(rows[0]).toMatchObject({ align: "right", row: 0 });
  });
});

describe("describeDay and formatting helpers", () => {
  it("describes school blocks, tags and next hint for screen readers", () => {
    const s = summarizeDay({ lessons: [lesson("08:20", "11:50"), lesson("13:30", "15:10")], events: [], date: TOMORROW, now: at(7) });
    expect(describeDay(s)).toBe("Schule 08:20 bis 11:50 und 13:30 bis 15:10, Nachmittag");
  });

  it("describes free, done, early and the next hint", () => {
    expect(describeDay(summarizeDay({ lessons: [], events: [], date: TODAY, now: at(9) }))).toBe("frei");
    expect(describeDay(summarizeDay({ lessons: [], events: [], date: TODAY, now: at(9) }), "nichts geplant")).toBe("nichts geplant");
    expect(describeDay(summarizeDay({ lessons: [lesson("08:00", "09:00")], events: [], date: TODAY, now: at(10) }))).toBe(
      "Schule 08:00 bis 09:00 (vorbei), fertig für heute"
    );
    expect(describeDay(summarizeDay({ lessons: [lesson("07:30", "09:00")], events: [], date: TODAY, now: at(7, 0) }))).toBe(
      "Schule 07:30 bis 09:00, Frühstunde, als Nächstes in 30 Min"
    );
  });

  it("says so when there is no school but only appointments", () => {
    const events = [event("e", "Arzt", at(14, 30), at(15))];
    expect(describeDay(summarizeDay({ lessons: [], events, date: TODAY, now: at(7) }))).toBe("keine Schule");
  });

  it("formats the short column heading date", () => {
    expect(formatShortDate(new Date(2026, 9, 8))).toBe("Do 8.10.");
    expect(formatShortDate(new Date(2026, 9, 9))).toBe("Fr 9.10.");
    expect(formatShortDate(new Date(2026, 0, 4))).toBe("So 4.1.");
  });

  it("detects whether an event touches a local day", () => {
    const e = event("e", "x", at(23, 30), at(1, 0, 9));
    expect(eventOverlapsDay(e, TODAY)).toBe(true);
    expect(eventOverlapsDay(e, TOMORROW)).toBe(true);
    expect(eventOverlapsDay(e, "2026-10-10")).toBe(false);
    expect(eventOverlapsDay(e, "2026-10-07")).toBe(false);
  });
});


describe("school breaks (Ferienzelle)", () => {
  const MIA = "child-mia";
  const LEO = "child-leo";
  const WEEKDAYS = [0, 1, 2, 3, 4];

  function brk(start: string, end: string, extra: Partial<DayBreak> = {}): DayBreak {
    return { title: "Herbstferien", kind: "ferien", start_date: start, end_date: end, child_user_ids: [], ...extra };
  }

  function day(date: string, breaks: DayBreak[], extra: { timetableDays?: number[]; events?: DayEvent[]; lessons?: DayLesson[] } = {}) {
    return summarizeDay({
      lessons: extra.lessons ?? [lesson("08:00", "11:50")],
      events: extra.events ?? [],
      date,
      now: at(6),
      breaks,
      timetableDays: extra.timetableDays ?? WEEKDAYS,
    });
  }

  it("is the holiday state: lessons are not drawn, no tags, the title and kind are carried", () => {
    const s = day(TODAY, [brk("2026-10-05", "2026-10-11")], { lessons: [lesson("07:00", "15:00")] });
    expect(s.state).toBe("holiday");
    expect(s.blocks).toEqual([]);
    expect(s.tags).toEqual([]);
    expect(s.holiday).toEqual({ title: "Herbstferien", kind: "ferien", hint: "bis So 11.10." });
  });

  it("is not a holiday outside the break (and the day keeps its lessons)", () => {
    const s = day(TODAY, [brk("2026-10-12", "2026-10-23")]);
    expect(s.holiday).toBeNull();
    expect(s.state).toBe("active");
    expect(s.blocks).toHaveLength(1);
  });

  it("covers both the first and the last day (inclusive)", () => {
    const breaks = [brk("2026-10-12", "2026-10-23")];
    expect(day("2026-10-12", breaks).state).toBe("holiday");
    expect(day("2026-10-23", breaks).state).toBe("holiday");
    expect(day("2026-10-11", breaks).state).not.toBe("holiday");
    expect(day("2026-10-24", breaks).state).not.toBe("holiday");
  });

  it("says 'bis ...' while the break continues the next day", () => {
    expect(day("2026-10-12", [brk("2026-10-12", "2026-10-25")]).holiday?.hint).toBe("bis So 25.10.");
    expect(day("2026-10-24", [brk("2026-10-12", "2026-10-25")]).holiday?.hint).toBe("bis So 25.10.");
  });

  it.each([
    ["Friday: the weekend is skipped", "2026-10-23", WEEKDAYS, "Schule wieder ab Mo 26.10."],
    ["Sunday: next day with lessons", "2026-10-25", WEEKDAYS, "Schule wieder ab Mo 26.10."],
    ["Wednesday: the next day", "2026-10-21", WEEKDAYS, "Schule wieder ab Do 22.10."],
    ["only Tuesday and Thursday have lessons", "2026-10-26", [1, 3], "Schule wieder ab Di 27.10."],
    ["no timetable known: simply the day after", "2026-10-23", [], "Schule wieder ab Sa 24.10."],
  ])("last day, %s", (_name, end, timetableDays, expected) => {
    const s = day(end, [brk("2026-10-12", end)], { timetableDays });
    expect(s.holiday?.hint).toBe(expected);
  });

  it("skips days covered by another break when looking for the first school day", () => {
    const breaks = [brk("2026-10-12", "2026-10-23"), brk("2026-10-26", "2026-10-26", { title: "Brückentag", kind: "schulfrei" })];
    expect(day("2026-10-23", breaks).holiday?.hint).toBe("Schule wieder ab Di 27.10.");
  });

  it("treats back-to-back breaks as one", () => {
    const breaks = [brk("2026-10-12", "2026-10-16", { title: "Woche 1" }), brk("2026-10-17", "2026-10-23", { title: "Woche 2" })];
    expect(day("2026-10-14", breaks).holiday?.hint).toBe("bis Fr 23.10.");
    expect(day("2026-10-16", breaks).holiday?.hint).toBe("bis Fr 23.10.");
    expect(day("2026-10-23", breaks).holiday?.hint).toBe("Schule wieder ab Mo 26.10.");
  });

  it("a single free day uses the free-day kind and points to the next school day", () => {
    const s = day("2026-10-14", [brk("2026-10-14", "2026-10-14", { title: "Weiterbildung", kind: "schulfrei" })]);
    expect(s.holiday).toEqual({ title: "Weiterbildung", kind: "schulfrei", hint: "Schule wieder ab Do 15.10." });
  });

  it("keeps the other appointments of the day as chips and only draws no lessons", () => {
    const events = [
      event("a", "Zahnarzt", at(15, 0, 8), at(16, 0, 8)),
      event("b", "Geburtstag", new Date(2026, 9, 8), new Date(2026, 9, 9), true),
    ];
    const s = day(TODAY, [brk("2026-10-05", "2026-10-11")], { events });
    expect(s.state).toBe("holiday");
    expect(s.chips.map((c) => c.title)).toEqual(["Geburtstag", "Zahnarzt"]);
    expect(s.blocks).toEqual([]);
  });

  it("a day without any lessons or appointments inside a break is still a holiday, not 'free'", () => {
    expect(day("2026-10-10", [brk("2026-10-05", "2026-10-11")], { lessons: [] }).state).toBe("holiday");
  });

  it("works for past days and future days alike", () => {
    expect(summarizeDay({ lessons: [], events: [], date: "2026-10-01", now: at(12), breaks: [brk("2026-09-28", "2026-10-02")] }).state).toBe("holiday");
    expect(day("2026-12-24", [brk("2026-12-21", "2027-01-01")]).holiday?.hint).toBe("bis Fr 1.1.");
  });

  it("without breaks nothing changes (holiday is null)", () => {
    const s = summarizeDay({ lessons: [lesson("08:00", "09:00")], events: [], date: TODAY, now: at(6) });
    expect(s.holiday).toBeNull();
    expect(s.state).toBe("active");
  });

  it("holidayForDay returns null for no covering break", () => {
    expect(holidayForDay([], TODAY)).toBeNull();
    expect(holidayForDay([brk("2026-10-12", "2026-10-13")], TODAY)).toBeNull();
  });

  it("describeDay mentions the holiday for screen readers", () => {
    const s = day(TODAY, [brk("2026-10-05", "2026-10-11")]);
    expect(describeDay(s)).toBe("Ferien: Herbstferien, bis So 11.10.");
    const free = day("2026-10-14", [brk("2026-10-14", "2026-10-14", { title: "Fortbildung", kind: "schulfrei" })]);
    expect(describeDay(free)).toBe("Schulfrei: Fortbildung, Schule wieder ab Do 15.10.");
  });

  describe("breaksForChild", () => {
    const all = brk("2026-10-12", "2026-10-23", { title: "alle" });
    const onlyMia = brk("2026-10-12", "2026-10-23", { title: "Mia", child_user_ids: [MIA] });
    const both = brk("2026-10-12", "2026-10-23", { title: "beide", child_user_ids: [LEO, MIA] });

    it("an empty child list applies to every child", () => {
      expect(breaksForChild([all], MIA)).toEqual([all]);
      expect(breaksForChild([all], LEO)).toEqual([all]);
    });

    it("a listed break applies only to the listed children", () => {
      expect(breaksForChild([all, onlyMia, both], MIA).map((b) => b.title)).toEqual(["alle", "Mia", "beide"]);
      expect(breaksForChild([all, onlyMia, both], LEO).map((b) => b.title)).toEqual(["alle", "beide"]);
      expect(breaksForChild([onlyMia], LEO)).toEqual([]);
    });

    it("without a known child only the breaks for everybody apply", () => {
      expect(breaksForChild([all, onlyMia], null)).toEqual([all]);
      expect(breaksForChild([all, onlyMia], undefined)).toEqual([all]);
    });
  });
});
