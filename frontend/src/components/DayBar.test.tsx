import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { summarizeDay, type DayBreak, type DayEvent, type DayLesson } from "../utils/dayBar";
import DayBar from "./DayBar";
import DaySummaryCell from "./DaySummaryCell";

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

const MORNING_AND_AFTERNOON = [lesson("08:20", "09:05"), lesson("09:05", "11:50"), lesson("13:30", "15:10")];

describe("DayBar", () => {
  it("draws one block per school section at its true position and width", () => {
    const summary = summarizeDay({ lessons: MORNING_AND_AFTERNOON, events: [], date: TOMORROW, now: at(7) });
    const { container } = render(<DayBar summary={summary} label="Lena, Morgen" dayName="Morgen" />);

    const blocks = container.querySelectorAll<HTMLElement>(".daybar-block");
    expect(blocks).toHaveLength(2);
    expect(blocks[0].style.left).toBe("13.33%");
    expect(blocks[0].style.width).toBe("35%");
    expect(blocks[1].style.left).toBe("65%");
    expect(blocks[1].style.width).toBe("16.67%");
    expect(container.querySelectorAll('[data-icon="school"]').length).toBeGreaterThan(0);
  });

  it("labels each block with its real times", () => {
    const summary = summarizeDay({ lessons: MORNING_AND_AFTERNOON, events: [], date: TOMORROW, now: at(7) });
    render(<DayBar summary={summary} label="Lena, Morgen" dayName="Morgen" />);
    expect(screen.getByText("08:20–11:50")).toBeInTheDocument();
    expect(screen.getByText("13:30–15:10")).toBeInTheDocument();
  });

  it("shows the real times in the label even when the block is clamped to the track edges", () => {
    const summary = summarizeDay({ lessons: [lesson("06:30", "18:30")], events: [], date: TOMORROW, now: at(7) });
    const { container } = render(<DayBar summary={summary} label="Mia, Morgen" dayName="Morgen" />);
    const block = container.querySelector<HTMLElement>(".daybar-block")!;
    expect(block.style.left).toBe("0%");
    expect(block.style.width).toBe("100%");
    expect(screen.getByText("06:30–18:30")).toBeInTheDocument();
  });

  it("has an accessible description naming person, day, school times and tags", () => {
    const summary = summarizeDay({ lessons: MORNING_AND_AFTERNOON, events: [], date: TOMORROW, now: at(7) });
    render(<DayBar summary={summary} label="Lena, Morgen" dayName="Morgen" />);
    expect(
      screen.getByRole("img", { name: "Lena, Morgen: Schule 08:20 bis 11:50 und 13:30 bis 15:10, Nachmittag" })
    ).toBeInTheDocument();
  });

  it("shows the Frühstunde and Nachmittag tags", () => {
    const summary = summarizeDay({ lessons: [lesson("07:30", "11:00"), lesson("13:30", "15:00")], events: [], date: TOMORROW, now: at(7) });
    render(<DayBar summary={summary} label="Mia, Morgen" dayName="Morgen" />);
    expect(screen.getByText("Frühstunde")).toHaveClass("daybar-tag--early");
    expect(screen.getByText("Nachmittag")).toHaveClass("daybar-tag--afternoon");
  });

  it("has no tags for an ordinary morning", () => {
    const summary = summarizeDay({ lessons: [lesson("08:20", "11:50")], events: [], date: TOMORROW, now: at(7) });
    render(<DayBar summary={summary} label="Mia, Morgen" dayName="Morgen" />);
    expect(screen.queryByText("Frühstunde")).not.toBeInTheDocument();
    expect(screen.queryByText("Nachmittag")).not.toBeInTheDocument();
  });

  it("greys out finished blocks and marks them with a check instead of the school icon", () => {
    const summary = summarizeDay({ lessons: MORNING_AND_AFTERNOON, events: [], date: TODAY, now: at(12, 0) });
    const { container } = render(<DayBar summary={summary} label="Mia, Heute" dayName="Heute" />);
    const blocks = container.querySelectorAll(".daybar-block");
    expect(blocks[0]).toHaveClass("daybar-block--done");
    expect(blocks[0].querySelector('[data-icon="check"]')).not.toBeNull();
    expect(blocks[1]).not.toHaveClass("daybar-block--done");
    expect(blocks[1].querySelector('[data-icon="school"]')).not.toBeNull();
  });

  it("rings the next school block and shows the minute hint", () => {
    const summary = summarizeDay({ lessons: [lesson("08:20", "11:50")], events: [], date: TODAY, now: at(7, 55) });
    const { container } = render(<DayBar summary={summary} label="Mia, Heute" dayName="Heute" />);
    expect(container.querySelector(".daybar-block")).toHaveClass("daybar-block--next");
    expect(screen.getByText("in 25 Min")).toBeInTheDocument();
  });

  it("does not ring a block while it is running", () => {
    const summary = summarizeDay({ lessons: [lesson("08:20", "11:50")], events: [], date: TODAY, now: at(9) });
    const { container } = render(<DayBar summary={summary} label="Mia, Heute" dayName="Heute" />);
    expect(container.querySelector(".daybar-block--next")).toBeNull();
  });

  it("shows only 'Fertig für heute' plus grey blocks when everything is over", () => {
    const summary = summarizeDay({ lessons: MORNING_AND_AFTERNOON, events: [], date: TODAY, now: at(16) });
    const { container } = render(<DayBar summary={summary} label="Mia, Heute" dayName="Heute" />);
    expect(screen.getByText("Fertig für heute")).toBeInTheDocument();
    expect(container.querySelectorAll(".daybar-block--done")).toHaveLength(2);
    expect(screen.getByRole("img")).toHaveAccessibleName(/fertig für heute/);
  });

  it("shows 'Heute frei' / 'Morgen frei' for a free day", () => {
    const today = summarizeDay({ lessons: [], events: [], date: TODAY, now: at(9) });
    const { unmount } = render(<DayBar summary={today} label="Mia, Heute" dayName="Heute" />);
    expect(screen.getByText("Heute frei")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Mia, Heute: frei" })).toBeInTheDocument();
    unmount();

    const tomorrow = summarizeDay({ lessons: [], events: [], date: TOMORROW, now: at(9) });
    render(<DayBar summary={tomorrow} label="Mia, Morgen" dayName="Morgen" />);
    expect(screen.getByText("Morgen frei")).toBeInTheDocument();
  });

  it("shows 'Nichts geplant' in the family variant", () => {
    const summary = summarizeDay({ lessons: [], events: [], date: TODAY, now: at(9) });
    render(<DayBar summary={summary} label="Familie, Heute" dayName="Heute" emptyVariant="family" />);
    expect(screen.getByText("Nichts geplant")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Familie, Heute: nichts geplant" })).toBeInTheDocument();
  });

  it("renders no track when the day only has appointments", () => {
    const summary = summarizeDay({ lessons: [], events: [event("e", "Arzt", at(14), at(15))], date: TODAY, now: at(9) });
    const { container } = render(<DayBar summary={summary} label="Mia, Heute" dayName="Heute" />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("DaySummaryCell", () => {
  it("shows time, bold time and title for a timed appointment", () => {
    const summary = summarizeDay({ lessons: [], events: [event("e", "Zahnarzt", at(14, 30), at(15))], date: TODAY, now: at(9) });
    const { container } = render(<DaySummaryCell summary={summary} label="Mia, Heute" dayName="Heute" />);
    const chip = container.querySelector(".day-chip")!;
    expect(chip.querySelector("strong")).toHaveTextContent("14:30");
    expect(chip).toHaveTextContent("Zahnarzt");
    expect(chip.querySelector('[data-icon="clock"]')).not.toBeNull();
  });

  it("shows an all-day appointment without a time and with an all-day icon", () => {
    const summary = summarizeDay({
      lessons: [],
      events: [event("d", "Schulreise", at(0), at(0, 0, 9), true)],
      date: TODAY,
      now: at(9),
    });
    const { container } = render(<DaySummaryCell summary={summary} label="Mia, Heute" dayName="Heute" />);
    const chip = container.querySelector(".day-chip")!;
    expect(chip.querySelector("strong")).toBeNull();
    expect(chip.querySelector('[data-icon="calendar"]')).not.toBeNull();
    expect(chip).toHaveTextContent("Schulreise");
    expect(chip).toHaveTextContent("ganztägig"); // for screen readers
  });

  it("shows at most two chips and a '+N' chip for the rest", () => {
    const events = [
      event("1", "Eins", at(9), at(10)),
      event("2", "Zwei", at(10, 30), at(11)),
      event("3", "Drei", at(12), at(13)),
      event("4", "Vier", at(14), at(15)),
    ];
    const summary = summarizeDay({ lessons: [], events, date: TODAY, now: at(8) });
    const { container } = render(<DaySummaryCell summary={summary} label="Mia, Heute" dayName="Heute" />);
    expect(container.querySelectorAll(".day-chip:not(.day-chip--more)")).toHaveLength(2);
    expect(screen.getByText("Eins")).toBeInTheDocument();
    expect(screen.getByText("Zwei")).toBeInTheDocument();
    expect(screen.queryByText("Drei")).not.toBeInTheDocument();
    const more = screen.getByText("+2");
    expect(more).toHaveAccessibleName("und 2 weitere Termine");
  });

  it("rings the next appointment and shows the hint", () => {
    const events = [event("1", "Arzt", at(8, 25), at(9)), event("2", "Später", at(15), at(16))];
    const summary = summarizeDay({ lessons: [], events, date: TODAY, now: at(8) });
    const { container } = render(<DaySummaryCell summary={summary} label="Mia, Heute" dayName="Heute" />);
    const chips = container.querySelectorAll(".day-chip");
    expect(chips[0]).toHaveClass("day-chip--next");
    expect(chips[0]).toHaveTextContent("in 25 Min");
    expect(chips[1]).not.toHaveClass("day-chip--next");
  });

  it("keeps the long title ellipsis-able and readable via title attribute", () => {
    const summary = summarizeDay({
      lessons: [],
      events: [event("1", "Sehr langer Termin beim Kinderarzt", at(9), at(10))],
      date: TODAY,
      now: at(8),
    });
    render(<DaySummaryCell summary={summary} label="Mia, Heute" dayName="Heute" />);
    expect(screen.getByText("Sehr langer Termin beim Kinderarzt")).toHaveClass("day-chip-title");
  });

  it("combines bar and chips and has no chips on a free day", () => {
    const summary = summarizeDay({ lessons: [lesson("08:20", "11:50")], events: [event("1", "Kita", at(14, 0, 9), at(15, 0, 9))], date: TOMORROW, now: at(8) });
    const { container } = render(<DaySummaryCell summary={summary} label="Mia, Morgen" dayName="Morgen" />);
    expect(container.querySelectorAll(".daybar-block")).toHaveLength(1);
    expect(container.querySelectorAll(".day-chip")).toHaveLength(1);

    const free = summarizeDay({ lessons: [], events: [], date: TOMORROW, now: at(8) });
    const { container: c2 } = render(<DaySummaryCell summary={free} label="Mia, Morgen" dayName="Morgen" />);
    expect(c2.querySelectorAll(".day-chip")).toHaveLength(0);
  });
});

describe("DayBar: Ferienzelle", () => {
  const FERIEN: DayBreak = {
    title: "Herbstferien",
    kind: "ferien",
    start_date: "2026-10-05",
    end_date: "2026-10-11",
    child_user_ids: [],
  };

  it("shows the title, the beach icon and 'bis ...' instead of the lesson blocks", () => {
    const summary = summarizeDay({ lessons: MORNING_AND_AFTERNOON, events: [], date: TODAY, now: at(7), breaks: [FERIEN] });
    const { container } = render(<DayBar summary={summary} label="Mia, Heute" dayName="Heute" />);

    const bar = screen.getByRole("img", { name: "Mia, Heute: Ferien: Herbstferien, bis So 11.10." });
    expect(bar).toHaveClass("daybar--holiday");
    expect(screen.getByText("Herbstferien")).toBeInTheDocument();
    expect(screen.getByText("bis So 11.10.")).toBeInTheDocument();
    expect(container.querySelector('[data-icon="beach"]')).not.toBeNull();
    expect(container.querySelectorAll(".daybar-block")).toHaveLength(0);
    expect(container.querySelectorAll(".daybar-tag")).toHaveLength(0);
  });

  it("uses the school-off icon and 'Schule wieder ab' for a free day on its last day", () => {
    const free: DayBreak = { ...FERIEN, title: "Weiterbildung", kind: "schulfrei", start_date: TODAY, end_date: TODAY };
    const summary = summarizeDay({ lessons: [], events: [], date: TODAY, now: at(7), breaks: [free], timetableDays: [0, 1, 2, 3, 4] });
    const { container } = render(<DayBar summary={summary} label="Mia, Heute" dayName="Heute" />);

    expect(screen.getByRole("img", { name: "Mia, Heute: Schulfrei: Weiterbildung, Schule wieder ab Fr 9.10." })).toBeInTheDocument();
    expect(container.querySelector('[data-icon="school-off"]')).not.toBeNull();
    expect(container.querySelector('[data-icon="beach"]')).toBeNull();
  });

  it("keeps the other appointments next to the Ferienzelle", () => {
    const summary = summarizeDay({
      lessons: MORNING_AND_AFTERNOON,
      events: [event("1", "Zahnarzt", at(15), at(16))],
      date: TODAY,
      now: at(7),
      breaks: [FERIEN],
    });
    const { container } = render(<DaySummaryCell summary={summary} label="Mia, Heute" dayName="Heute" />);

    expect(container.querySelector(".daybar--holiday")).not.toBeNull();
    expect(screen.getByText("Zahnarzt")).toBeInTheDocument();
    expect(container.querySelectorAll(".daybar-block")).toHaveLength(0);
  });
});
