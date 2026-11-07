import type { SchoolBreak } from "../api/schoolBreaks";

function parts(isoDate: string): { d: string; m: string; y: string } {
  const [y, m, d] = isoDate.split("-");
  return { d, m, y };
}

/** "12.10.2026" for a single day, "12.10. – 23.10.2026" for a range (years on both ends when they differ). */
export function formatBreakRange(start: string, end: string): string {
  const a = parts(start);
  const b = parts(end);
  if (start === end) return `${a.d}.${a.m}.${a.y}`;
  if (a.y !== b.y) return `${a.d}.${a.m}.${a.y} – ${b.d}.${b.m}.${b.y}`;
  return `${a.d}.${a.m}. – ${b.d}.${b.m}.${b.y}`;
}

/** Upcoming (and running) breaks by start date first, then the finished ones, most recent first. */
export function sortBreaks(breaks: SchoolBreak[], todayIso: string): { upcoming: SchoolBreak[]; past: SchoolBreak[] } {
  const upcoming = breaks.filter((b) => b.end_date >= todayIso).sort((a, b) => a.start_date.localeCompare(b.start_date));
  const past = breaks.filter((b) => b.end_date < todayIso).sort((a, b) => b.start_date.localeCompare(a.start_date));
  return { upcoming, past };
}
