import { apiFetch } from "./client";

/** Which kind of day a phase applies to. "both" = school days and free days. */
export type DayPhaseType = "school" | "free" | "both";

export type DayPhase = {
  id: string;
  name: string;
  icon: string | null;
  /** Local time of day, "HH:MM:SS". */
  start_time: string;
  end_time: string;
  day_type: DayPhaseType;
  sort_order: number;
};

export type DayPhaseInput = {
  name: string;
  icon?: string | null;
  start_time: string;
  end_time: string;
  day_type: DayPhaseType;
  sort_order?: number;
};

const BASE = "/day-phases";

export function fetchDayPhases(): Promise<DayPhase[]> {
  return apiFetch<DayPhase[]>(BASE);
}

export function createDayPhase(input: DayPhaseInput): Promise<DayPhase> {
  return apiFetch<DayPhase>(BASE, { method: "POST", body: JSON.stringify(input) });
}

export function updateDayPhase(id: string, input: Partial<DayPhaseInput>): Promise<DayPhase> {
  return apiFetch<DayPhase>(`${BASE}/${id}`, { method: "PATCH", body: JSON.stringify(input) });
}

export function deleteDayPhase(id: string): Promise<void> {
  return apiFetch<void>(`${BASE}/${id}`, { method: "DELETE" });
}

/** Seeds the standard phases; the server answers 409 when the family already has phases. */
export function seedDefaultDayPhases(): Promise<DayPhase[]> {
  return apiFetch<DayPhase[]>(`${BASE}/defaults`, { method: "POST" });
}
