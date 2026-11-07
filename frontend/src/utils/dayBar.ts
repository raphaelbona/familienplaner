import { localIsoDateOffset, toLocalIsoDate } from "./date";
import { WEEKDAY_LABELS } from "./week";

/** The day bar's fixed scale: 07:00 to 17:00. */
export const TRACK_START = 7 * 60;
export const TRACK_END = 17 * 60;
/** Lessons separated by at most this many minutes belong to one school block. */
export const MERGE_GAP_MINUTES = 20;
/** A school day starting before 07:45 gets the "Frühstunde" tag. */
export const EARLY_BEFORE = 7 * 60 + 45;
/** A block starting at or after 12:30 gets the "Nachmittag" tag. */
export const AFTERNOON_FROM = 12 * 60 + 30;
/** Instant (or zero-length) events count as running for this long. */
const MIN_EVENT_MINUTES = 15;
/** Narrowest drawn block, in percent of the track (so a block outside 07-17 stays visible as a stub). */
const MIN_BLOCK_PERCENT = 4;
const DAY_MINUTES = 24 * 60;

/** The timetable model has no kind/category field, so every lesson is "school". */
export type BlockKind = "school";

export type DayLesson = { start_time: string; end_time: string };

export type DayEvent = {
  id: string;
  title: string;
  start: string;
  end: string;
  all_day: boolean;
};

export type DayBlock = { start: number; end: number; kind: BlockKind; done: boolean };

export type DayTag = "early" | "afternoon";

export type DayChip = {
  id: string;
  title: string;
  /** "HH:MM" start, null for all-day (or day-spanning) events. */
  time: string | null;
  allDay: boolean;
  startMin: number | null;
};

export type DayNext =
  | { type: "block"; index: number; startMin: number; hint: string }
  | { type: "chip"; id: string; startMin: number; hint: string };

export type DayState = "free" | "done" | "active" | "holiday";

export type BreakKind = "ferien" | "schulfrei";

/** The part of a school break (GET /school-breaks) the day bar needs. */
export type DayBreak = {
  title: string;
  kind: BreakKind;
  /** Inclusive local dates, "YYYY-MM-DD". */
  start_date: string;
  end_date: string;
  /** Empty = every child. */
  child_user_ids: string[];
};

/** A day inside a school break: lessons are not drawn, the cell shows this instead. */
export type DayHoliday = {
  title: string;
  kind: BreakKind;
  /** "bis So 25.10." while the break goes on, "Schule wieder ab Mo 26.10." on its last day. */
  hint: string | null;
};

export type DaySummary = {
  blocks: DayBlock[];
  tags: DayTag[];
  /** Not yet finished appointments: all-day ones first, then timed by start. */
  chips: DayChip[];
  next: DayNext | null;
  state: DayState;
  holiday: DayHoliday | null;
};

export const BREAK_KIND_LABELS: Record<BreakKind, string> = { ferien: "Ferien", schulfrei: "Schulfrei" };

export const TAG_LABELS: Record<DayTag, string> = { early: "Frühstunde", afternoon: "Nachmittag" };

export function formatMinutes(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

/** "HH:MM" or "HH:MM:SS" to minutes of the day; null when unusable. */
function parseTime(time: string): number | null {
  const match = /^(\d{1,2}):(\d{2})/.exec(time);
  if (!match) return null;
  const minutes = Number(match[1]) * 60 + Number(match[2]);
  return minutes >= 0 && minutes <= DAY_MINUTES ? minutes : null;
}

/** "Do 8.10." (Monday-first labels, no leading zeros). */
export function formatShortDate(date: Date): string {
  const weekday = WEEKDAY_LABELS[(date.getDay() + 6) % 7];
  return `${weekday} ${date.getDate()}.${date.getMonth() + 1}.`;
}

/** "in 25 Min", "in 1 Std", "in 1 Std 30 Min"; "jetzt" for 0. */
export function formatInMinutes(minutes: number): string {
  if (minutes <= 0) return "jetzt";
  if (minutes < 60) return `in ${minutes} Min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m === 0 ? `in ${h} Std` : `in ${h} Std ${m} Min`;
}

type EventSpan = { start: Date; end: Date };

function parseEvent(event: DayEvent): EventSpan | null {
  const start = new Date(event.start);
  const end = new Date(event.end);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return null;
  return { start, end: end < start ? start : end };
}

function localDayStart(isoDate: string): Date {
  const [y, m, d] = isoDate.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function nextIsoDate(isoDate: string): string {
  return localIsoDateOffset(1, localDayStart(isoDate));
}

/** Whether an event touches the given local day. All-day events cover whole local dates (their end is
 * exclusive, whether stored at local or UTC midnight); timed events are checked against the day's span. */
export function eventOverlapsDay(event: DayEvent, isoDate: string): boolean {
  const span = parseEvent(event);
  if (!span) return false;
  if (event.all_day) {
    const firstDay = toLocalIsoDate(span.start);
    let endExclusive = toLocalIsoDate(span.end);
    if (endExclusive <= firstDay) endExclusive = nextIsoDate(firstDay);
    return firstDay <= isoDate && isoDate < endExclusive;
  }
  const dayStart = localDayStart(isoDate);
  const dayEnd = localDayStart(nextIsoDate(isoDate));
  if (span.start >= dayEnd) return false;
  if (span.start >= dayStart) return true;
  return span.end > dayStart;
}

/** The breaks that apply to one child (an empty child list means every child). */
export function breaksForChild(breaks: DayBreak[], childId: string | null | undefined): DayBreak[] {
  return breaks.filter((b) => b.child_user_ids.length === 0 || (childId != null && b.child_user_ids.includes(childId)));
}

function breakCovers(brk: DayBreak, isoDate: string): boolean {
  return brk.start_date <= isoDate && isoDate <= brk.end_date;
}

/** Python-style weekday (0=Monday..6=Sunday) of a local "YYYY-MM-DD". */
function isoWeekday(isoDate: string): number {
  return (localDayStart(isoDate).getDay() + 6) % 7;
}

/**
 * The holiday cell of a day, or null when no break covers it. Consecutive breaks (one starting the day after
 * the other ends) count as one. `timetableDays` are the weekdays (0=Monday) on which the child has lessons:
 * "Schule wieder ab" names the next such day after the break, or simply the day after when unknown.
 */
export function holidayForDay(breaks: DayBreak[], isoDate: string, timetableDays: number[] = []): DayHoliday | null {
  const covering = breaks.filter((b) => breakCovers(b, isoDate));
  if (covering.length === 0) return null;
  const main = covering.reduce((best, b) => (b.end_date > best.end_date ? b : best));

  let end = main.end_date;
  for (let grown = true; grown; ) {
    grown = false;
    const dayAfter = nextIsoDate(end);
    for (const b of breaks) {
      if (b.start_date <= dayAfter && b.end_date > end) {
        end = b.end_date;
        grown = true;
      }
    }
  }

  let hint: string;
  if (isoDate < end) {
    hint = `bis ${formatShortDate(localDayStart(end))}`;
  } else {
    let back = nextIsoDate(end);
    if (timetableDays.length > 0) {
      for (let i = 0; i < 14; i++) {
        const candidate = localIsoDateOffset(i, localDayStart(back));
        if (timetableDays.includes(isoWeekday(candidate)) && !breaks.some((b) => breakCovers(b, candidate))) {
          back = candidate;
          break;
        }
      }
    }
    hint = `Schule wieder ab ${formatShortDate(localDayStart(back))}`;
  }
  return { title: main.title, kind: main.kind, hint };
}

function mergeLessons(lessons: DayLesson[]): { start: number; end: number }[] {
  const ranges = lessons
    .map((l) => ({ start: parseTime(l.start_time), end: parseTime(l.end_time) }))
    .filter((r): r is { start: number; end: number } => r.start !== null && r.end !== null && r.end > r.start)
    .sort((a, b) => a.start - b.start);

  const merged: { start: number; end: number }[] = [];
  for (const range of ranges) {
    const last = merged[merged.length - 1];
    if (last && range.start - last.end <= MERGE_GAP_MINUTES) {
      last.end = Math.max(last.end, range.end);
    } else {
      merged.push({ ...range });
    }
  }
  return merged;
}

type Candidate = { type: "block"; index: number; startMin: number } | { type: "chip"; id: string; startMin: number };

/**
 * Everything the day bar shows for ONE person on ONE local day.
 * `lessons` are that day's timetable entries (the caller picks the weekday), `events` any appointments (the
 * day filter is applied here), `date` is the local "YYYY-MM-DD" and `now` the injected clock.
 */
export function summarizeDay({
  lessons,
  events,
  date,
  now,
  breaks = [],
  timetableDays,
}: {
  lessons: DayLesson[];
  events: DayEvent[];
  date: string;
  now: Date;
  /** The school breaks that apply to this person (see breaksForChild). On a covered day the lessons are not drawn. */
  breaks?: DayBreak[];
  /** Weekdays (0=Monday) with lessons in this person's timetable, for the "Schule wieder ab" hint. */
  timetableDays?: number[];
}): DaySummary {
  const holiday = holidayForDay(breaks, date, timetableDays);
  if (holiday) lessons = [];
  const today = toLocalIsoDate(now);
  const isToday = date === today;
  // Minutes elapsed on that day: past days are over completely, future days have not started.
  const nowMin = isToday ? now.getHours() * 60 + now.getMinutes() : date < today ? DAY_MINUTES + 1 : -1;

  const blocks: DayBlock[] = mergeLessons(lessons).map((r) => ({
    start: r.start,
    end: r.end,
    kind: "school",
    done: r.end <= nowMin,
  }));

  const tags: DayTag[] = [];
  if (blocks.length > 0 && blocks[0].start < EARLY_BEFORE) tags.push("early");
  if (blocks.some((b) => b.start >= AFTERNOON_FROM)) tags.push("afternoon");

  const dayStart = localDayStart(date);
  const dayEnd = localDayStart(nextIsoDate(date));

  const allDayChips: DayChip[] = [];
  const timedChips: DayChip[] = [];
  let hasEvents = false;
  let hasOpenAllDay = false;
  let allTimedEventsOver = true;

  for (const event of events) {
    if (!eventOverlapsDay(event, date)) continue;
    const span = parseEvent(event)!;
    hasEvents = true;

    const continuesFromBefore = span.start < dayStart;
    if (event.all_day || continuesFromBefore) {
      hasOpenAllDay = true;
      allDayChips.push({ id: event.id, title: event.title, time: null, allDay: true, startMin: null });
      continue;
    }

    const startMin = Math.round((span.start.getTime() - dayStart.getTime()) / 60000);
    const endMin = span.end >= dayEnd ? DAY_MINUTES : Math.round((span.end.getTime() - dayStart.getTime()) / 60000);
    const over = Math.max(endMin, startMin + MIN_EVENT_MINUTES) <= nowMin;
    if (over) continue;
    allTimedEventsOver = false;
    timedChips.push({ id: event.id, title: event.title, time: formatMinutes(startMin), allDay: false, startMin });
  }

  allDayChips.sort((a, b) => a.title.localeCompare(b.title, "de"));
  timedChips.sort((a, b) => (a.startMin ?? 0) - (b.startMin ?? 0));
  const chips = [...allDayChips, ...timedChips];

  let state: DayState;
  if (holiday) {
    state = "holiday";
  } else if (blocks.length === 0 && !hasEvents) {
    state = "free";
  } else if (
    nowMin >= 0 &&
    !hasOpenAllDay &&
    allTimedEventsOver &&
    blocks.every((b) => b.done)
  ) {
    state = "done";
  } else {
    state = "active";
  }

  let next: DayNext | null = null;
  if (isToday) {
    const candidates: Candidate[] = [
      ...blocks.flatMap((b, index): Candidate[] => (!b.done && b.start > nowMin ? [{ type: "block", index, startMin: b.start }] : [])),
      ...timedChips.flatMap((c): Candidate[] => (c.startMin !== null && c.startMin > nowMin ? [{ type: "chip", id: c.id, startMin: c.startMin }] : [])),
    ];
    // Stable sort: a school block wins a tie against an appointment starting at the same minute.
    candidates.sort((a, b) => a.startMin - b.startMin);
    const first = candidates[0];
    if (first) {
      const hint = formatInMinutes(first.startMin - nowMin);
      next = first.type === "block" ? { ...first, hint } : { ...first, hint };
    }
  }

  return { blocks, tags, chips, next, state, holiday };
}

export type BlockGeometry = {
  /** Percent of the track width. */
  left: number;
  width: number;
};

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

/** Position and size of a block on the 07:00-17:00 track; times outside the track are clamped to its edges. */
export function blockGeometry(block: Pick<DayBlock, "start" | "end">): BlockGeometry {
  const span = TRACK_END - TRACK_START;
  const clamp = (minutes: number) => Math.min(TRACK_END, Math.max(TRACK_START, minutes));
  const start = clamp(block.start);
  const end = clamp(block.end);
  const width = Math.max(((end - start) / span) * 100, MIN_BLOCK_PERCENT);
  const left = Math.min(((start - TRACK_START) / span) * 100, 100 - width);
  return { left: round2(left), width: round2(width) };
}

/** Faint tick positions (percent) at 08:00, 12:00 and 16:00. */
export const TRACK_TICKS = [8, 12, 16].map((hour) => ((hour * 60 - TRACK_START) / (TRACK_END - TRACK_START)) * 100);

export type LabelLayout = { left: number; align: "left" | "right"; row: number };

/** Places the "HH:MM-HH:MM" labels under their blocks without overlap: a label starts at its block's left
 * edge (right-aligned when it would leave the track) and drops to a further row if it would touch the previous
 * label there. `labelPercent` is the estimated label width in percent of the track. */
export function layoutLabels(items: { left: number; width: number }[], labelPercent: number | number[]): LabelLayout[] {
  const rowEnds: number[] = [];
  return items.map((item, i) => {
    const w = Array.isArray(labelPercent) ? labelPercent[i] : labelPercent;
    const rightAligned = item.left + w > 100;
    const start = rightAligned ? 100 - w : item.left;
    const end = rightAligned ? 100 : item.left + w;

    let row = rowEnds.findIndex((rowEnd) => start >= rowEnd + 2);
    if (row === -1) row = rowEnds.length;
    rowEnds[row] = end;
    return { left: round2(item.left), align: rightAligned ? "right" : "left", row };
  });
}

/** Screen-reader sentence for the bar (without the person/day prefix): school times, tags, state. */
export function describeDay(summary: DaySummary, emptyLabel = "frei"): string {
  if (summary.state === "holiday" && summary.holiday) {
    const { kind, title, hint } = summary.holiday;
    return [`${BREAK_KIND_LABELS[kind]}: ${title}`, hint].filter(Boolean).join(", ");
  }
  if (summary.state === "free") return emptyLabel;

  const parts: string[] = [];
  if (summary.blocks.length > 0) {
    const times = summary.blocks.map((b) => `${formatMinutes(b.start)} bis ${formatMinutes(b.end)}${b.done ? " (vorbei)" : ""}`);
    parts.push(`Schule ${times.join(" und ")}`);
  } else {
    parts.push("keine Schule");
  }
  for (const tag of summary.tags) parts.push(TAG_LABELS[tag]);
  if (summary.state === "done") parts.push("fertig für heute");
  if (summary.next?.type === "block") parts.push(`als Nächstes ${summary.next.hint}`);
  return parts.join(", ");
}
