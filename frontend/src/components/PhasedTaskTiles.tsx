import type { DayPhase } from "../api/dayPhases";
import { groupByPhase, phaseStartLabel } from "../utils/dayPhases";
import type { TaskStatus } from "../utils/taskDisplay";
import TaskTile, { type TileTask } from "./TaskTile";

type PhasedTaskTilesProps = {
  /** Today's tiles in their final order; that order is kept inside each phase. */
  tasks: TileTask[];
  phases: DayPhase[];
  /** Local "now": decides which phase is current and which ones have not started yet. */
  now: Date;
  onToggle: (taskId: string, status: TaskStatus) => void;
  busyTaskId: string | null;
};

/** Today's tiles grouped by day phase: phase-less tiles first without heading, then one block per phase
 *  (icon, name, "ab 07:00") in start order. The current phase is highlighted, later ones are dimmed and
 *  cannot be ticked off yet. Without phases it is just the plain tile grid. */
export default function PhasedTaskTiles({ tasks, phases, now, onToggle, busyTaskId }: PhasedTaskTilesProps) {
  const groups = groupByPhase(tasks, phases, now);

  if (!groups.some((group) => group.phase)) {
    return (
      <div className="task-tile-grid">
        {tasks.map((task) => (
          <TaskTile key={task.id} task={task} onToggle={onToggle} busy={busyTaskId === task.id} />
        ))}
      </div>
    );
  }

  return (
    <div className="phase-groups">
      {groups.map((group) => {
        const phase = group.phase;
        const label = phase ? phaseStartLabel(phase) : null;
        return (
          <section
            key={phase?.id ?? "no-phase"}
            className={phase ? `phase-group phase-group--${group.state}` : "phase-group-loose"}
            aria-label={phase ? phase.name : undefined}
          >
            {phase && (
              <h4 className="phase-heading">
                <span className="phase-heading-icon" aria-hidden="true">
                  {phase.icon || "🕒"}
                </span>
                <span className="phase-heading-name">{phase.name}</span>
                <span className="phase-heading-time">{label}</span>
              </h4>
            )}
            <div className="task-tile-grid">
              {group.items.map((task) => (
                <TaskTile
                  key={task.id}
                  task={task}
                  onToggle={onToggle}
                  busy={busyTaskId === task.id}
                  notBefore={group.state === "upcoming" ? label : null}
                />
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}
