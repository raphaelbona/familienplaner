/** YYYY-MM-DD from LOCAL date parts (toISOString would use UTC and be a day off around local midnight). */
export function toLocalIsoDate(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

/** Local date `days` days from `from` (default: now); negative values go back. */
export function localIsoDateOffset(days: number, from: Date = new Date()): string {
  const date = new Date(from.getFullYear(), from.getMonth(), from.getDate() + days);
  return toLocalIsoDate(date);
}

export function todayIsoDate(): string {
  return toLocalIsoDate(new Date());
}

export function tomorrowIsoDate(): string {
  return localIsoDateOffset(1);
}
