export type TaskKind = "basis" | "bonus" | "geld";

export type TaskDisplayTemplate = {
  kind: TaskKind;
  points_value: number;
  bonus_multiplier: number;
  money_value_chf?: number | null;
};

/** "expired" (verfallen): a missed task that the next occurrence replaced. Never shown on boards or counted. */
export type TaskStatus = "open" | "completed" | "approved" | "expired";

/** Drops expired tasks (the server also returns them when no status filter is sent). */
export function withoutExpired<T extends { status: TaskStatus }>(rows: T[]): T[] {
  return rows.filter((row) => row.status !== "expired");
}

export function effectiveTaskPoints(template: TaskDisplayTemplate): number {
  if (template.kind !== "bonus") return template.points_value;
  return Math.round(template.points_value * template.bonus_multiplier);
}

export function taskRewardLabel(template: TaskDisplayTemplate): string {
  if (template.kind === "basis") {
    return "Basisaufgabe - gehört zum Alltag";
  }

  if (template.kind === "geld") {
    return `+CHF ${(template.money_value_chf ?? 0).toFixed(2)} möglich`;
  }

  return `+${effectiveTaskPoints(template)} Punkte möglich`;
}

export function taskKindLabel(template: TaskDisplayTemplate): string {
  if (template.kind === "basis") return "Basis";
  if (template.kind === "geld") return "Geld";
  return "Bonus";
}

export function taskStatusLabel(status: TaskStatus): string {
  if (status === "completed") return "Wartet auf Eltern";
  if (status === "approved") return "Bestätigt";
  if (status === "expired") return "Verfallen";
  return "Heute noch möglich";
}

export function taskStatusHelp(status: TaskStatus, template: TaskDisplayTemplate): string {
  if (status === "completed") return "Du hast abgegeben. Mama oder Papa schaut es an.";
  if (status === "approved") return "Schon bestätigt.";
  if (status === "expired") return "Verpasst und von der nächsten Aufgabe ersetzt.";
  return taskRewardLabel(template);
}
