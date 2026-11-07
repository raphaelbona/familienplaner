import { apiFetch } from "./client";

export type SchoolBreakKind = "ferien" | "schulfrei";

export type SchoolBreak = {
  id: string;
  title: string;
  kind: SchoolBreakKind;
  /** Inclusive local dates, "YYYY-MM-DD". */
  start_date: string;
  end_date: string;
  /** Empty = every child. A child's own login only ever gets [] or its own id. */
  child_user_ids: string[];
  /** "manual" (entered by a parent) | "google" (derived from an all-day Google event, changed in Google). */
  source: "manual" | "google";
};

export type SchoolBreakInput = {
  title: string;
  kind: SchoolBreakKind;
  start_date: string;
  end_date: string;
  child_user_ids: string[];
};

const BASE = "/school-breaks";

/** Breaks overlapping from..to (local ISO dates); the server defaults to a window around today. */
export function fetchSchoolBreaks(from?: string, to?: string): Promise<SchoolBreak[]> {
  const query = new URLSearchParams();
  if (from) query.set("from", from);
  if (to) query.set("to", to);
  const suffix = query.toString();
  return apiFetch<SchoolBreak[]>(suffix ? `${BASE}?${suffix}` : BASE);
}

export function createSchoolBreak(input: SchoolBreakInput): Promise<SchoolBreak> {
  return apiFetch<SchoolBreak>(BASE, { method: "POST", body: JSON.stringify(input) });
}

export function updateSchoolBreak(id: string, input: Partial<SchoolBreakInput>): Promise<SchoolBreak> {
  return apiFetch<SchoolBreak>(`${BASE}/${id}`, { method: "PATCH", body: JSON.stringify(input) });
}

export function deleteSchoolBreak(id: string): Promise<void> {
  return apiFetch<void>(`${BASE}/${id}`, { method: "DELETE" });
}
