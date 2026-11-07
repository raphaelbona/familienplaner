import { useLayoutEffect, useRef, useState } from "react";
import {
  TAG_LABELS,
  TRACK_TICKS,
  blockGeometry,
  describeDay,
  formatMinutes,
  layoutLabels,
  type DaySummary,
} from "../utils/dayBar";
import { BeachIcon, CalendarOffIcon, CheckIcon, SchoolIcon, SchoolOffIcon, SmileIcon, SunIcon, ClockIcon } from "./dayIcons";

type DayBarProps = {
  summary: DaySummary;
  /** Screen-reader prefix: who and which day, e.g. "Lena, Morgen". */
  label: string;
  /** "Heute" or "Morgen" -- used in the free-day text. */
  dayName: string;
  /** The family row says "Nichts geplant" instead of "<Tag> frei". */
  emptyVariant?: "free" | "family";
};

const LABEL_ROW_PX = 15;
/** Estimated width of an "HH:MM–HH:MM" label in px, to keep neighbours from overlapping. */
const LABEL_WIDTH_PX = 74;
/** Narrowest icon-carrying block, in percent of the track. */
const ICON_MIN_PERCENT = 10;
/** Used until (or without) a measurement, e.g. in tests. */
const FALLBACK_TRACK_PX = 160;

function useTrackWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(FALLBACK_TRACK_PX);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      // offsetWidth is in the element's own CSS px, unaffected by the wall tablet's zoom.
      if (el.offsetWidth > 0) setWidth(el.offsetWidth);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return { ref, width };
}

function DayBarTrack({ summary }: { summary: DaySummary }) {
  const { ref, width } = useTrackWidth();
  const geometries = summary.blocks.map(blockGeometry);
  const labelPercent = (LABEL_WIDTH_PX / width) * 100;
  const labels = layoutLabels(geometries, labelPercent);
  const rows = labels.reduce((max, l) => Math.max(max, l.row + 1), 1);
  const nextBlock = summary.next?.type === "block" ? summary.next.index : -1;

  return (
    <>
      <div className="daybar-track" ref={ref}>
        {TRACK_TICKS.map((tick) => (
          <span key={tick} className="daybar-tick" style={{ left: `${tick}%` }} />
        ))}
        {summary.blocks.map((block, i) => {
          const g = geometries[i];
          const classes = ["daybar-block"];
          if (block.done) classes.push("daybar-block--done");
          if (i === nextBlock) classes.push("daybar-block--next");
          return (
            <span key={`${block.start}-${block.end}`} className={classes.join(" ")} style={{ left: `${g.left}%`, width: `${g.width}%` }}>
              {g.width >= ICON_MIN_PERCENT && (block.done ? <CheckIcon size={13} /> : <SchoolIcon size={14} />)}
            </span>
          );
        })}
      </div>
      <div className="daybar-labels" style={{ height: rows * LABEL_ROW_PX }}>
        {summary.blocks.map((block, i) => {
          const layout = labels[i];
          const position = layout.align === "left" ? { left: `${layout.left}%` } : { right: 0 };
          return (
            <span
              key={`${block.start}-${block.end}`}
              className={`daybar-label${block.done ? " daybar-label--done" : ""}`}
              style={{ ...position, top: layout.row * LABEL_ROW_PX }}
            >
              {formatMinutes(block.start)}–{formatMinutes(block.end)}
            </span>
          );
        })}
      </div>
    </>
  );
}

/**
 * One day of one person as a picture: school collapsed to blocks on a fixed 07:00-17:00 track (times
 * underneath, tags above) or an icon for an empty / finished day. Appointments are separate chips
 * (DaySummaryCell). Renders nothing for a day with appointments only. A day inside a school break is a yellow
 * "Ferienzelle" (icon, title, "bis ..." / "Schule wieder ab ...") instead of the lesson blocks.
 */
export default function DayBar({ summary, label, dayName, emptyVariant = "free" }: DayBarProps) {
  const family = emptyVariant === "family";
  const ariaLabel = `${label}: ${describeDay(summary, family ? "nichts geplant" : "frei")}`;

  if (summary.state === "holiday" && summary.holiday) {
    const { kind, title, hint } = summary.holiday;
    return (
      <div className="daybar daybar--holiday" role="img" aria-label={ariaLabel}>
        <div className="daybar-holiday">
          <span className="daybar-holiday-icon">{kind === "ferien" ? <BeachIcon size={20} /> : <SchoolOffIcon size={20} />}</span>
          <span className="daybar-holiday-text">
            <strong title={title}>{title}</strong>
            {hint && <small>{hint}</small>}
          </span>
        </div>
      </div>
    );
  }

  if (summary.state === "free") {
    return (
      <div className="daybar daybar--empty" role="img" aria-label={ariaLabel}>
        <div className="daybar-empty">
          {family ? <CalendarOffIcon size={16} /> : <SmileIcon size={16} />}
          <span>{family ? "Nichts geplant" : `${dayName} frei`}</span>
        </div>
      </div>
    );
  }

  if (summary.blocks.length === 0 && summary.state === "active") return null;

  const hint = summary.next?.type === "block" ? summary.next.hint : null;

  return (
    <div className="daybar" role="img" aria-label={ariaLabel}>
      {summary.blocks.length > 0 && (summary.tags.length > 0 || hint) && (
        <div className="daybar-tags">
          {summary.tags.map((tag) => (
            <span key={tag} className={`daybar-tag daybar-tag--${tag}`}>
              {tag === "early" ? <SunIcon size={11} /> : <ClockIcon size={11} />}
              {TAG_LABELS[tag]}
            </span>
          ))}
          {hint && <span className="daybar-hint">{hint}</span>}
        </div>
      )}
      {summary.blocks.length > 0 && <DayBarTrack summary={summary} />}
      {summary.state === "done" && (
        <div className="daybar-done">
          <CheckIcon size={15} />
          <span>Fertig für heute</span>
        </div>
      )}
    </div>
  );
}
