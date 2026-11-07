import { WEEKDAY_LABELS } from "../utils/week";

export type DayState = "done" | "partial" | "open" | "none";

type WeekStripProps = {
  days: DayState[]; // 7 entries, Monday first
  todayIndex: number;
  color: string;
};

const STATE_STYLE: Record<DayState, (color: string) => React.CSSProperties> = {
  done: (color) => ({ background: color, border: `2px solid ${color}` }),
  partial: (color) => ({ background: `${color}55`, border: `2px solid ${color}` }),
  open: (color) => ({ background: "transparent", border: `2px solid ${color}` }),
  none: () => ({ background: "transparent", border: "2px solid var(--border)" }),
};

/** A Mo-So row of small dots showing each day's task completion state for one person. */
export default function WeekStrip({ days, todayIndex, color }: WeekStripProps) {
  return (
    <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
      {days.map((state, i) => (
        <div key={i} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 3, flex: 1 }}>
          <span
            style={{
              fontSize: 10,
              fontWeight: i === todayIndex ? 700 : 500,
              color: i === todayIndex ? color : "var(--text-muted)",
            }}
          >
            {WEEKDAY_LABELS[i]}
          </span>
          <span
            aria-hidden="true"
            style={{
              width: 14,
              height: 14,
              borderRadius: "50%",
              boxSizing: "border-box",
              ...STATE_STYLE[state](color),
            }}
          />
        </div>
      ))}
    </div>
  );
}
