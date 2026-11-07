import { toLocalIsoDate } from "./date";

export const WEEKDAY_LABELS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];

/** The current week's Monday and Sunday, as ISO date strings (backend day-of-week
 * convention: Monday-first, matching the timetable/task recurrence models). */
export function currentWeekBounds(): { monday: string; sunday: string; days: string[] } {
  const now = new Date();
  const pythonWeekday = (now.getDay() + 6) % 7; // 0=Monday..6=Sunday
  const monday = new Date(now);
  monday.setDate(now.getDate() - pythonWeekday);

  const days: string[] = [];
  for (let i = 0; i < 7; i++) {
    const d = new Date(monday);
    d.setDate(monday.getDate() + i);
    days.push(toLocalIsoDate(d));
  }

  return { monday: days[0], sunday: days[6], days };
}
