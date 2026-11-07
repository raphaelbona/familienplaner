import type { DayChip, DaySummary } from "../utils/dayBar";
import DayBar from "./DayBar";
import { CalendarIcon, ClockIcon } from "./dayIcons";

const MAX_CHIPS = 2;

type DaySummaryCellProps = {
  summary: DaySummary;
  /** Screen-reader prefix: who and which day, e.g. "Lena, Morgen". */
  label: string;
  dayName: string;
  emptyVariant?: "free" | "family";
};

function Chip({ chip, isNext, hint }: { chip: DayChip; isNext: boolean; hint: string | null }) {
  return (
    <li className={`day-chip${isNext ? " day-chip--next" : ""}`}>
      {chip.allDay ? <CalendarIcon size={13} /> : <ClockIcon size={13} />}
      {chip.time ? <strong>{chip.time}</strong> : <span className="day-chip-sr">ganztägig: </span>}
      <span className="day-chip-title" title={chip.title}>
        {chip.title}
      </span>
      {isNext && hint && <span className="day-chip-hint">{hint}</span>}
    </li>
  );
}

/** A day of one person: the day bar plus up to two appointment chips ("+N" for the rest). */
export default function DaySummaryCell({ summary, label, dayName, emptyVariant }: DaySummaryCellProps) {
  const shown = summary.chips.slice(0, MAX_CHIPS);
  const rest = summary.chips.slice(MAX_CHIPS);
  const nextChipId = summary.next?.type === "chip" ? summary.next.id : null;

  return (
    <div className="day-cell">
      <DayBar summary={summary} label={label} dayName={dayName} emptyVariant={emptyVariant} />
      {summary.chips.length > 0 && (
        <ul className="day-chips" aria-label={`${label}: Termine`}>
          {shown.map((chip) => (
            <Chip key={chip.id} chip={chip} isNext={chip.id === nextChipId} hint={summary.next?.hint ?? null} />
          ))}
          {rest.length > 0 && (
            <li
              className="day-chip day-chip--more"
              aria-label={`und ${rest.length} weitere ${rest.length === 1 ? "Termin" : "Termine"}`}
              title={rest.map((c) => (c.time ? `${c.time} ${c.title}` : c.title)).join("\n")}
            >
              +{rest.length}
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
