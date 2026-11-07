import type { DayPhase } from "../api/dayPhases";
import PhasedTaskTiles from "./PhasedTaskTiles";
import type { TileTask } from "./TaskTile";
import type { TaskStatus } from "../utils/taskDisplay";
import WeekStrip, { type DayState } from "./WeekStrip";

export type { TileTask } from "./TaskTile";

type FamilyMemberTileProps = {
  avatar: string;
  name: string;
  color: string;
  todayDone: number;
  todayTotal: number;
  /** Only tasks due today. */
  todayTasks: TileTask[];
  weekDays: DayState[];
  todayIndex: number;
  /** Tap on a tile: status "open" = mark done, "completed" = undo. */
  onToggleTask: (taskId: string, status: TaskStatus) => void;
  completingTaskId: string | null;
  onSelect?: () => void;
  /** The family's day phases; today's tiles are grouped by them. Without phases the tiles stay one plain grid. */
  phases?: DayPhase[];
  /** Local "now" for the current/upcoming phase (default: the time of rendering). */
  now?: Date;
};

/** A wall-dashboard tile for one family member: avatar, short progress ("2/4" + thin bar in the
 * person colour), today's tasks as big picture tiles, and a Mo-So week strip. */
export default function FamilyMemberTile({
  avatar,
  name,
  color,
  todayDone,
  todayTotal,
  todayTasks,
  weekDays,
  todayIndex,
  onToggleTask,
  completingTaskId,
  onSelect,
  phases = [],
  now,
}: FamilyMemberTileProps) {
  const percent = todayTotal > 0 ? Math.round((todayDone / todayTotal) * 100) : 0;

  return (
    <div className="family-tile" style={{ borderTopColor: color }}>
      <button
        type="button"
        className="family-tile-header"
        onClick={onSelect}
        disabled={!onSelect}
        style={{ cursor: onSelect ? "pointer" : "default" }}
      >
        <span
          aria-hidden="true"
          className="family-tile-avatar"
          style={{ background: `${color}22`, border: `2.5px solid ${color}` }}
        >
          {avatar}
        </span>
        <span className="family-tile-name">{name}</span>
        {todayTotal > 0 && (
          <span className="family-tile-progress" style={{ color }} aria-label={`${todayDone} von ${todayTotal} erledigt`}>
            {todayDone}/{todayTotal}
          </span>
        )}
      </button>

      {todayTotal > 0 && (
        <div className="family-tile-bar" role="presentation" aria-hidden="true">
          <div className="family-tile-bar-fill" style={{ width: `${percent}%`, background: color }} />
        </div>
      )}

      {todayTasks.length > 0 ? (
        <PhasedTaskTiles
          tasks={todayTasks}
          phases={phases}
          now={now ?? new Date()}
          onToggle={onToggleTask}
          busyTaskId={completingTaskId}
        />
      ) : (
        <div className="family-tile-free">
          <span aria-hidden="true">🌴</span> Heute frei
        </div>
      )}

      <WeekStrip days={weekDays} todayIndex={todayIndex} color={color} />
    </div>
  );
}
