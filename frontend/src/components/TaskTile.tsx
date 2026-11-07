import { effectiveTaskPoints, type TaskDisplayTemplate, type TaskStatus } from "../utils/taskDisplay";

export type TileTask = {
  id: string;
  title: string;
  status: TaskStatus;
  icon: string | null;
  /** The day phase this tile belongs to (only used to group tiles). */
  phase_id?: string | null;
  /** Reward data, only used for the tiny badge on bonus/geld tasks. */
  template?: Pick<TaskDisplayTemplate, "kind" | "points_value" | "bonus_multiplier" | "money_value_chf">;
};

type TaskTileProps = {
  task: TileTask;
  /** Tap on an open tile = "ich bin fertig", tap on a done tile = undo. Never called for approved tiles. */
  onToggle: (taskId: string, status: TaskStatus) => void;
  /** A request for this tile is running. */
  busy?: boolean;
  /** "ab 19:00": the task's day phase has not started yet, so the tile is dimmed and cannot be ticked off. */
  notBefore?: string | null;
};

const FALLBACK_ICON = "⭐";

/** Tiny reward badge text for bonus/geld tasks; basis tasks (mandatory chores) get none. */
export function tileRewardBadge(task: TileTask): string | null {
  const template = task.template;
  if (!template) return null;
  if (template.kind === "geld") {
    const chf = template.money_value_chf ?? 0;
    return chf > 0 ? `CHF ${Number.isInteger(chf) ? chf : chf.toFixed(2)}` : null;
  }
  if (template.kind === "bonus") {
    const points = effectiveTaskPoints(template);
    return points > 0 ? `+${points}` : null;
  }
  return null;
}

function tileAriaLabel(task: TileTask, notBefore: string | null): string {
  if (task.status === "expired") return `${task.title}, verfallen`;
  if (notBefore && task.status === "open") return `${task.title}, noch nicht dran, ${notBefore}`;
  if (task.status === "approved") return `${task.title}, von Eltern bestätigt`;
  if (task.status === "completed") return `${task.title}, erledigt, antippen zum Zurücknehmen`;
  return `${task.title}, offen, antippen wenn erledigt`;
}

/** A big, picture-first task button. The whole tile is one button: state is shown by colour and
 * symbol (empty ring / green check / lock), not by text. */
export default function TaskTile({ task, onToggle, busy = false, notBefore = null }: TaskTileProps) {
  const approved = task.status === "approved";
  const expired = task.status === "expired";
  const upcoming = Boolean(notBefore) && task.status === "open";
  const locked = approved || expired || upcoming;
  const done = task.status === "completed";
  const reward = tileRewardBadge(task);

  return (
    <button
      type="button"
      className={`task-tile task-tile--${task.status}${upcoming ? " task-tile--upcoming" : ""}${busy ? " task-tile--busy" : ""}`}
      aria-label={tileAriaLabel(task, notBefore)}
      aria-pressed={approved || done}
      aria-busy={busy || undefined}
      disabled={locked || busy}
      onClick={() => {
        if (!locked && !busy) onToggle(task.id, task.status);
      }}
    >
      <span className="task-tile-icon" aria-hidden="true">
        {task.icon || FALLBACK_ICON}
        <span className="task-tile-state">{approved ? "🔒" : done ? "✓" : ""}</span>
      </span>
      <span className="task-tile-title">{task.title}</span>
      {reward && (
        <span className="task-tile-reward" aria-hidden="true">
          {reward}
        </span>
      )}
      {upcoming && <span className="task-tile-from">{notBefore}</span>}
      {busy && <span className="task-tile-busy" aria-hidden="true" />}
    </button>
  );
}
