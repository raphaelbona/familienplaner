import type { DayPhase } from "../api/dayPhases";

export type PhaseState = "past" | "current" | "upcoming";

/** Anything that may belong to a phase (a task instance or a tile). */
export type PhaseBound = { phase_id?: string | null };

export type PhaseGroup<T> = {
  /** null = items without a phase (or whose phase is unknown): shown first, without heading. */
  phase: DayPhase | null;
  /** Only set for real phases and only meaningful for today. */
  state: PhaseState | null;
  items: T[];
};

/** Minutes since midnight of "HH:MM" or "HH:MM:SS"; null if not a valid time. */
export function parseHhmm(value: string | null | undefined): number | null {
  const match = /^(\d{1,2}):(\d{2})(?::\d{2})?$/.exec((value ?? "").trim());
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) return null;
  return hours * 60 + minutes;
}

/** "07:00" from "07:00:00" (anything unparseable is returned unchanged). */
export function formatHhmm(value: string): string {
  const minutes = parseHhmm(value);
  if (minutes === null) return value;
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

/** "ab 07:00" */
export function phaseStartLabel(phase: Pick<DayPhase, "start_time">): string {
  return `ab ${formatHhmm(phase.start_time)}`;
}

/** "06:00–08:00" */
export function phaseRangeLabel(phase: Pick<DayPhase, "start_time" | "end_time">): string {
  return `${formatHhmm(phase.start_time)}–${formatHhmm(phase.end_time)}`;
}

/** Earlier phases first: by start time, then the parents' own order, then name. */
export function comparePhases(a: DayPhase, b: DayPhase): number {
  return (
    (parseHhmm(a.start_time) ?? 0) - (parseHhmm(b.start_time) ?? 0) ||
    a.sort_order - b.sort_order ||
    a.name.localeCompare(b.name, "de")
  );
}

export function sortPhases(phases: DayPhase[]): DayPhase[] {
  return [...phases].sort(comparePhases);
}

/** Where `now` (local time) is relative to a phase: before its start = upcoming, from start until
 *  (excluding) end = current, otherwise past. A phase with unreadable times counts as current (never blocks). */
export function phaseState(phase: Pick<DayPhase, "start_time" | "end_time">, now: Date): PhaseState {
  const start = parseHhmm(phase.start_time);
  const parsedEnd = parseHhmm(phase.end_time);
  if (start === null || parsedEnd === null) return "current";
  // An end at or before the start (e.g. 20:00-00:00) runs to the end of the day.
  const end = parsedEnd <= start ? 24 * 60 : parsedEnd;
  const minutes = now.getHours() * 60 + now.getMinutes();
  if (minutes < start) return "upcoming";
  if (minutes < end) return "current";
  return "past";
}

/** Groups items by their phase: the phase-less ones first (no heading), then the phases in start order.
 *  Within a group the incoming order is kept; empty phases are left out. Items pointing to a phase that is
 *  not in `phases` (deleted, or the phases failed to load) count as phase-less. */
export function groupByPhase<T extends PhaseBound>(items: T[], phases: DayPhase[], now: Date): PhaseGroup<T>[] {
  const byId = new Map(phases.map((phase) => [phase.id, phase]));
  const loose: T[] = [];
  const perPhase = new Map<string, T[]>();
  for (const item of items) {
    const phase = item.phase_id ? byId.get(item.phase_id) : undefined;
    if (!phase) {
      loose.push(item);
      continue;
    }
    perPhase.set(phase.id, [...(perPhase.get(phase.id) ?? []), item]);
  }
  const groups: PhaseGroup<T>[] = [];
  if (loose.length > 0) groups.push({ phase: null, state: null, items: loose });
  for (const phase of sortPhases(phases)) {
    const list = perPhase.get(phase.id);
    if (list) groups.push({ phase, state: phaseState(phase, now), items: list });
  }
  return groups;
}

/** The phase of an item, if it is known. */
export function phaseOf(item: PhaseBound, phases: DayPhase[]): DayPhase | null {
  return item.phase_id ? (phases.find((phase) => phase.id === item.phase_id) ?? null) : null;
}

/** "ab 19:00" if the item's phase has not started yet today, else null. Only for tasks due today. */
export function notBeforeLabel(item: PhaseBound, phases: DayPhase[], now: Date): string | null {
  const phase = phaseOf(item, phases);
  if (!phase) return null;
  return phaseState(phase, now) === "upcoming" ? phaseStartLabel(phase) : null;
}
