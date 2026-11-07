import { FormEvent, useEffect, useMemo, useState } from "react";
import { apiFetch, getToken, decodeRoleFromToken, decodeUserIdFromToken, getFamilyId } from "../api/client";
import IconPicker from "../components/IconPicker";
import DayPhasesCard from "../components/DayPhasesCard";
import { fetchDayPhases, type DayPhase } from "../api/dayPhases";
import { groupByPhase, notBeforeLabel, phaseOf, phaseStartLabel, sortPhases } from "../utils/dayPhases";
import {
  taskKindLabel,
  taskRewardLabel,
  taskStatusHelp,
  taskStatusLabel,
  withoutExpired,
  type TaskStatus,
} from "../utils/taskDisplay";
import { currentWeekBounds, WEEKDAY_LABELS } from "../utils/week";
import { localIsoDateOffset, todayIsoDate, tomorrowIsoDate } from "../utils/date";

type TaskTemplate = {
  id: string;
  title: string;
  kind: "basis" | "bonus" | "geld";
  points_value: number;
  bonus_multiplier: number;
  money_value_chf: number;
  icon: string | null;
  /** Day phases the task is split into (one tile per phase and day); empty = the whole day. */
  phase_ids?: string[];
  /** true: missed recurring tasks pile up; false: a missed task expires when the next one is due. */
  carry_over?: boolean;
};

type TaskInstance = {
  id: string;
  assignee_user_id: string;
  due_date: string;
  status: TaskStatus;
  phase_id?: string | null;
  template: TaskTemplate;
};

type ChildAccount = {
  id: string;
  display_name: string;
  avatar_key: string | null;
};

const STATUS_ICONS: Record<TaskInstance["status"], string> = {
  open: "⭐",
  completed: "👍",
  approved: "✅",
  expired: "⌛"
};

const STATUS_PILL_ICONS: Record<TaskInstance["status"], string> = {
  open: "⭐",
  completed: "👍",
  approved: "✅",
  expired: "⌛"
};

const KIND_ICONS: Record<TaskTemplate["kind"], string> = {
  basis: "🏠",
  bonus: "⭐",
  geld: "💰"
};

const TEMPLATE_KIND_LABELS: Record<TaskTemplate["kind"], string> = {
  basis: "Basis",
  bonus: "Bonus",
  geld: "Geld"
};

const PHASE_PICKER_GROUPS = [
  { type: "school", label: "Schultage", icon: "🏫" },
  { type: "free", label: "Freie Tage", icon: "🏖️" },
] as const;

const FILTER_ICONS: Record<TaskFilter, string> = {
  today: "☀️",
  tomorrow: "🌙",
  week: "📅",
  waiting: "👍",
  done: "✅"
};

const STATUS_CHIP_CLASS: Record<TaskInstance["status"], string> = {
  open: "",
  completed: "sky",
  approved: "leaf",
  expired: "muted"
};

type TaskFilter = "today" | "tomorrow" | "week" | "waiting" | "done";

/** Date first, then day phase (earlier phases first, phase-less tasks before them), then title. */
function sortChildTasks(a: TaskInstance, b: TaskInstance, phaseRank: Map<string, number>): number {
  const dateCompare = a.due_date.localeCompare(b.due_date);
  if (dateCompare !== 0) return dateCompare;
  const rankA = a.phase_id ? (phaseRank.get(a.phase_id) ?? -1) : -1;
  const rankB = b.phase_id ? (phaseRank.get(b.phase_id) ?? -1) : -1;
  if (rankA !== rankB) return rankA - rankB;
  return a.template.title.localeCompare(b.template.title, "de-CH");
}

function childEmptyCopy(filter: TaskFilter): { headline: string; subtext: string } {
  if (filter === "waiting") {
    return { headline: "Alles abgegeben", subtext: "Gerade wartet nichts auf Mama oder Papa." };
  }
  if (filter === "done") {
    return { headline: "Noch nichts bestätigt", subtext: "Bestätigte Aufgaben erscheinen hier." };
  }
  if (filter === "tomorrow") {
    return { headline: "Morgen ist frei", subtext: "Für morgen steht gerade keine Aufgabe an." };
  }
  if (filter === "week") {
    return { headline: "Ruhige Woche", subtext: "Diese Woche ist gerade nichts offen." };
  }
  return { headline: "Alles geschafft!", subtext: "Für heute ist gerade nichts offen." };
}

function familyEmptyCopy(filter: TaskFilter): { headline: string; subtext: string } {
  if (filter === "waiting") return { headline: "Nichts wartet", subtext: "Gerade muss nichts freigegeben werden." };
  if (filter === "done") return { headline: "Noch nichts bestätigt", subtext: "Bestätigte Aufgaben erscheinen hier." };
  if (filter === "tomorrow") return { headline: "Morgen ist leer", subtext: "Für morgen sind keine Aufgaben geplant." };
  if (filter === "week") return { headline: "Ruhige Woche", subtext: "Diese Woche stehen keine Aufgaben an." };
  return { headline: "Heute ist frei", subtext: "Für heute stehen keine Aufgaben an." };
}

function formatDueLabel(isoDate: string, todayIso: string, tomorrowIso: string): string {
  if (isoDate === todayIso) return "Heute";
  if (isoDate === tomorrowIso) return "Morgen";
  return new Date(isoDate).toLocaleDateString("de-CH", { weekday: "short", day: "numeric", month: "numeric" });
}

function taskCardIcon(instance: TaskInstance): string {
  if (instance.status !== "open") return STATUS_ICONS[instance.status];
  return instance.template.icon || STATUS_ICONS.open;
}

export default function Tasks() {
  const [instances, setInstances] = useState<TaskInstance[]>([]);
  const [error, setError] = useState<string | null>(null);

  // Parent UI state
  const [templates, setTemplates] = useState<TaskTemplate[]>([]);
  const [childAccounts, setChildAccounts] = useState<ChildAccount[]>([]);
  const [completedInstances, setCompletedInstances] = useState<TaskInstance[]>([]);

  // Template form state
  const [templateTitle, setTemplateTitle] = useState("");
  const [templateKind, setTemplateKind] = useState<"basis" | "bonus" | "geld">("basis");
  const [templatePoints, setTemplatePoints] = useState("");
  const [templateMultiplier, setTemplateMultiplier] = useState("1");
  const [templateMoney, setTemplateMoney] = useState("");
  const [templateIcon, setTemplateIcon] = useState<string | null>(null);
  const [templatePhaseIds, setTemplatePhaseIds] = useState<string[]>([]);
  const [templateCarryOver, setTemplateCarryOver] = useState(false);
  const [creatingTemplate, setCreatingTemplate] = useState(false);
  // Template management: edit mode reuses the creation form; delete asks inline first
  const [editingTemplateId, setEditingTemplateId] = useState<string | null>(null);
  const [templateFormError, setTemplateFormError] = useState<string | null>(null);
  const [templateListMessage, setTemplateListMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [deletingTemplateId, setDeletingTemplateId] = useState<string | null>(null);

  // Task assignment form state
  const [selectedTemplate, setSelectedTemplate] = useState("");
  const [selectedChildIds, setSelectedChildIds] = useState<string[]>([]);
  const [dueDate, setDueDate] = useState("");
  const [recurrenceRule, setRecurrenceRule] = useState<"" | "daily" | "weekly">("");
  const [selectedWeekdays, setSelectedWeekdays] = useState<number[]>([]);
  const [assigningTask, setAssigningTask] = useState(false);

  // Approval state
  const [approvingId, setApprovingId] = useState<string | null>(null);

  // Success feedback
  const [celebrateMessage, setCelebrateMessage] = useState<string | null>(null);
  const [taskFilter, setTaskFilter] = useState<TaskFilter>("today");

  // Day phases: today's cards are grouped by them. Parents get them from the management card below, everyone else
  // loads them here. A failing request just means "no phases" -- the board works without.
  const [phases, setPhases] = useState<DayPhase[]>([]);
  // Ticks every minute so a phase that starts while the page is open becomes tickable without a reload.
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  const token = getToken();
  const role = decodeRoleFromToken(token || "");
  const ownUserId = token ? decodeUserIdFromToken(token) : null;
  const isChild = role === "child";
  const isDashboard = role === "dashboard";
  const isParent = role === "parent_admin" || role === "parent_member";
  const familyId = getFamilyId();

  function loadInstances() {
    let query: string;
    if (role === "child" && ownUserId) {
      // Children only ever see their own tasks.
      query = `?assignee_user_id=${ownUserId}`;
    } else {
      // Parents keep the family-wide overview, but bounded to the next 7 days —
      // recurring daily/weekly tasks otherwise flood this list with months of rows.
      query = `?due_after=${todayIsoDate()}&due_before=${localIsoDateOffset(7)}`;
    }

    apiFetch<TaskInstance[]>(`/tasks/instances${query}`)
      .then((rows) => setInstances(withoutExpired(rows)))
      .catch((err) => setError(err instanceof Error ? err.message : "Aufgaben konnten nicht geladen werden"));
  }

  function loadParentData() {
    Promise.all([
      apiFetch<TaskTemplate[]>("/tasks/templates"),
      apiFetch<ChildAccount[]>(`/families/${getFamilyId()}/child-accounts`),
      apiFetch<TaskInstance[]>("/tasks/instances?status_filter=completed"),
    ])
      .then(([templatesData, childrenData, completedData]) => {
        setTemplates(templatesData);
        setChildAccounts(childrenData);
        setCompletedInstances(completedData);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Daten konnten nicht geladen werden"));
  }

  function loadFamilyChildren() {
    if (!familyId) return;
    apiFetch<ChildAccount[]>(`/families/${familyId}/child-accounts`)
      .then(setChildAccounts)
      .catch((err) => setError(err instanceof Error ? err.message : "Kinder konnten nicht geladen werden"));
  }

  useEffect(() => {
    loadInstances();
    if (isParent) {
      loadParentData();
    } else {
      fetchDayPhases()
        .then((rows) => setPhases(Array.isArray(rows) ? rows : []))
        .catch(() => setPhases([]));
      if (isDashboard) loadFamilyChildren();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function markComplete(id: string) {
    const finishedTask = instances.find((instance) => instance.id === id);
    try {
      await apiFetch(`/tasks/instances/${id}/complete`, { method: "POST" });
      loadInstances();
      const rewardText =
        finishedTask && finishedTask.template.kind !== "basis"
          ? `${taskRewardLabel(finishedTask.template)}. Wartet jetzt auf Eltern.`
          : "Wartet jetzt auf Eltern.";
      setCelebrateMessage(`Gut gemacht! ${rewardText}`);
      window.setTimeout(() => setCelebrateMessage(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Das hat nicht geklappt, versuch's nochmal.");
    }
  }

  async function handleCreateTemplate(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setCreatingTemplate(true);
    try {
      const payload: Record<string, string | number | boolean | string[]> = {
        title: templateTitle,
        kind: templateKind,
        phase_ids: templatePhaseIds,
        carry_over: templateCarryOver,
      };

      if (templateKind === "basis") {
        payload.points_value = 0;
      } else if (templateKind === "bonus") {
        const points = parseInt(templatePoints, 10);
        if (isNaN(points) || points <= 0) {
          setError("Punkte müssen größer als 0 sein");
          setCreatingTemplate(false);
          return;
        }
        payload.points_value = points;
      }

      if (templateKind === "bonus") {
        const multiplier = parseFloat(templateMultiplier);
        if (isNaN(multiplier) || multiplier <= 0 || multiplier > 5) {
          setError("Bonus-Multiplikator muss zwischen 0 und 5 liegen");
          setCreatingTemplate(false);
          return;
        }
        payload.bonus_multiplier = multiplier;
      }

      if (templateKind === "geld") {
        const money = parseFloat(templateMoney);
        if (isNaN(money) || money <= 0) {
          setError("Betrag muss größer als 0 sein");
          setCreatingTemplate(false);
          return;
        }
        payload.money_value_chf = money;
      }

      if (templateIcon) {
        payload.icon = templateIcon;
      }

      await apiFetch("/tasks/templates", {
        method: "POST",
        body: JSON.stringify(payload),
      });

      setTemplateTitle("");
      setTemplateKind("basis");
      setTemplatePoints("");
      setTemplateMultiplier("1");
      setTemplateMoney("");
      setTemplateIcon(null);
      setTemplatePhaseIds([]);
      setTemplateCarryOver(false);
      loadParentData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Vorlage konnte nicht erstellt werden");
    } finally {
      setCreatingTemplate(false);
    }
  }

  function resetTemplateForm() {
    setTemplateTitle("");
    setTemplateKind("basis");
    setTemplatePoints("");
    setTemplateMultiplier("1");
    setTemplateMoney("");
    setTemplateIcon(null);
    setTemplatePhaseIds([]);
    setTemplateCarryOver(false);
  }

  function togglePhase(phaseId: string) {
    setTemplatePhaseIds((prev) => (prev.includes(phaseId) ? prev.filter((id) => id !== phaseId) : [...prev, phaseId]));
  }

  function startEditTemplate(template: TaskTemplate) {
    setEditingTemplateId(template.id);
    setTemplateFormError(null);
    setTemplateListMessage(null);
    setConfirmDeleteId(null);
    setTemplateTitle(template.title);
    setTemplateKind(template.kind);
    setTemplatePoints(template.kind === "bonus" ? String(template.points_value) : "");
    setTemplateMultiplier(String(template.bonus_multiplier ?? 1));
    setTemplateMoney(template.kind === "geld" ? String(template.money_value_chf) : "");
    setTemplateIcon(template.icon ?? null);
    setTemplatePhaseIds(template.phase_ids ?? []);
    setTemplateCarryOver(template.carry_over ?? false);
    document.getElementById("template-form")?.scrollIntoView?.({ block: "nearest" });
  }

  function cancelEditTemplate() {
    setEditingTemplateId(null);
    setTemplateFormError(null);
    resetTemplateForm();
  }

  /** Economic fields as the create form would send them for the chosen kind, or an error message. */
  function readTemplateEconomics():
    | { fields: Pick<TaskTemplate, "kind" | "points_value" | "bonus_multiplier" | "money_value_chf"> }
    | { error: string } {
    if (templateKind === "basis") {
      return { fields: { kind: "basis", points_value: 0, bonus_multiplier: 1, money_value_chf: 0 } };
    }
    if (templateKind === "bonus") {
      const points = parseInt(templatePoints, 10);
      if (isNaN(points) || points <= 0) return { error: "Punkte müssen größer als 0 sein" };
      const multiplier = parseFloat(templateMultiplier);
      if (isNaN(multiplier) || multiplier <= 0 || multiplier > 5) {
        return { error: "Bonus-Multiplikator muss zwischen 0 und 5 liegen" };
      }
      return { fields: { kind: "bonus", points_value: points, bonus_multiplier: multiplier, money_value_chf: 0 } };
    }
    const money = parseFloat(templateMoney);
    if (isNaN(money) || money <= 0) return { error: "Betrag muss größer als 0 sein" };
    return { fields: { kind: "geld", points_value: 0, bonus_multiplier: 1, money_value_chf: money } };
  }

  function showTemplateMessage(tone: "ok" | "error", text: string) {
    setTemplateListMessage({ tone, text });
    if (tone === "ok") window.setTimeout(() => setTemplateListMessage(null), 4000);
  }

  async function handleSaveTemplate(event: FormEvent) {
    event.preventDefault();
    const original = templates.find((t) => t.id === editingTemplateId);
    if (!original) {
      cancelEditTemplate();
      return;
    }
    setTemplateFormError(null);
    const title = templateTitle.trim();
    if (!title) {
      setTemplateFormError("Titel darf nicht leer sein");
      return;
    }
    const economics = readTemplateEconomics();
    if ("error" in economics) {
      setTemplateFormError(economics.error);
      return;
    }

    // Only send what actually changed; the server decides whether economics may still change.
    const changes: Record<string, string | number | boolean | string[] | null> = {};
    if (title !== original.title) changes.title = title;
    if (templateIcon !== (original.icon ?? null)) changes.icon = templateIcon;
    const originalPhaseIds = original.phase_ids ?? [];
    if (
      templatePhaseIds.length !== originalPhaseIds.length ||
      templatePhaseIds.some((id) => !originalPhaseIds.includes(id))
    ) {
      changes.phase_ids = templatePhaseIds;
    }
    if (templateCarryOver !== (original.carry_over ?? false)) changes.carry_over = templateCarryOver;
    for (const [key, value] of Object.entries(economics.fields)) {
      if (original[key as keyof TaskTemplate] !== value) changes[key] = value;
    }

    if (Object.keys(changes).length === 0) {
      cancelEditTemplate();
      return;
    }

    setCreatingTemplate(true);
    try {
      await apiFetch(`/tasks/templates/${original.id}`, { method: "PATCH", body: JSON.stringify(changes) });
      cancelEditTemplate();
      showTemplateMessage("ok", "Vorlage gespeichert");
      loadParentData();
      loadInstances();
    } catch (err) {
      setTemplateFormError(err instanceof Error ? err.message : "Vorlage konnte nicht gespeichert werden");
    } finally {
      setCreatingTemplate(false);
    }
  }

  async function handleDeleteTemplate(template: TaskTemplate) {
    setTemplateListMessage(null);
    setDeletingTemplateId(template.id);
    try {
      const response = await apiFetch<{ result: "deleted" | "archived" }>(`/tasks/templates/${template.id}`, {
        method: "DELETE",
      });
      setConfirmDeleteId(null);
      if (editingTemplateId === template.id) cancelEditTemplate();
      showTemplateMessage(
        "ok",
        response?.result === "archived" ? "Vorlage ausgeblendet – wird noch verwendet" : "Vorlage gelöscht"
      );
      if (selectedTemplate === template.id) setSelectedTemplate("");
      loadParentData();
      loadInstances();
    } catch (err) {
      setConfirmDeleteId(null);
      showTemplateMessage("error", err instanceof Error ? err.message : "Vorlage konnte nicht gelöscht werden");
    } finally {
      setDeletingTemplateId(null);
    }
  }

  function toggleChild(childId: string) {
    setSelectedChildIds((prev) =>
      prev.includes(childId) ? prev.filter((id) => id !== childId) : [...prev, childId]
    );
  }

  function toggleWeekday(day: number) {
    setSelectedWeekdays((prev) => (prev.includes(day) ? prev.filter((d) => d !== day) : [...prev, day].sort()));
  }

  async function handleAssignTask(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setAssigningTask(true);
    try {
      if (!selectedTemplate || selectedChildIds.length === 0 || !dueDate) {
        setError("Alle Felder sind erforderlich");
        setAssigningTask(false);
        return;
      }

      await apiFetch("/tasks/instances", {
        method: "POST",
        body: JSON.stringify({
          template_id: selectedTemplate,
          assignee_user_ids: selectedChildIds,
          due_date: dueDate,
          recurrence_rule: recurrenceRule || null,
          weekdays: recurrenceRule === "weekly" && selectedWeekdays.length > 0 ? selectedWeekdays : undefined,
        }),
      });

      setSelectedTemplate("");
      setSelectedChildIds([]);
      setDueDate("");
      setRecurrenceRule("");
      setSelectedWeekdays([]);
      loadParentData();
      loadInstances();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Aufgabe konnte nicht zugewiesen werden");
    } finally {
      setAssigningTask(false);
    }
  }

  async function handleApproveInstance(id: string) {
    setError(null);
    setApprovingId(id);
    try {
      await apiFetch(`/tasks/instances/${id}/approve`, { method: "POST" });
      loadParentData();
      loadInstances();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Aufgabe konnte nicht freigegeben werden");
    } finally {
      setApprovingId(null);
    }
  }

  const todayIso = todayIsoDate();
  const tomorrowIso = tomorrowIsoDate();
  const { days: weekDays } = currentWeekBounds();
  const phaseRank = useMemo(() => new Map(sortPhases(phases).map((phase, index) => [phase.id, index])), [phases]);
  const childById = useMemo(() => new Map(childAccounts.map((child) => [child.id, child])), [childAccounts]);
  const orderedChildColumns = useMemo(
    () =>
      [...childAccounts].sort((a, b) => a.display_name.localeCompare(b.display_name, "de-CH")),
    [childAccounts]
  );

  const filterOptions: { id: TaskFilter; label: string; count: number }[] = [
    {
      id: "today",
      label: "Heute",
      count: instances.filter((instance) =>
        isChild ? instance.due_date === todayIso && instance.status === "open" : instance.due_date === todayIso
      ).length,
    },
    {
      id: "tomorrow",
      label: "Morgen",
      count: instances.filter((instance) =>
        isChild ? instance.due_date === tomorrowIso && instance.status === "open" : instance.due_date === tomorrowIso
      ).length,
    },
    {
      id: "week",
      label: "Diese Woche",
      count: instances.filter((instance) =>
        isChild ? weekDays.includes(instance.due_date) && instance.status === "open" : weekDays.includes(instance.due_date)
      ).length,
    },
    {
      id: "waiting",
      label: "Wartet",
      count: isParent
        ? completedInstances.length
        : instances.filter((instance) => instance.status === "completed").length,
    },
    {
      id: "done",
      label: "Geschafft",
      count: instances.filter((instance) => instance.status === "approved").length,
    },
  ];

  const boardSourceInstances = isParent && taskFilter === "waiting" ? completedInstances : instances;
  const filteredInstances = [...boardSourceInstances]
    .filter((instance) => {
      if (taskFilter === "today") return instance.due_date === todayIso && (isChild ? instance.status === "open" : true);
      if (taskFilter === "tomorrow") return instance.due_date === tomorrowIso && (isChild ? instance.status === "open" : true);
      if (taskFilter === "week") return weekDays.includes(instance.due_date) && (isChild ? instance.status === "open" : true);
      if (taskFilter === "waiting") return instance.status === "completed";
      return instance.status === "approved";
    })
    .sort((a, b) => {
      const childA = childById.get(a.assignee_user_id)?.display_name ?? "";
      const childB = childById.get(b.assignee_user_id)?.display_name ?? "";
      const childCompare = childA.localeCompare(childB, "de-CH");
      if (!isChild && childCompare !== 0) return childCompare;
      return sortChildTasks(a, b, phaseRank);
    });

  // A child sees at most five cards -- except for today with day phases, where every phase has to stay reachable.
  const limitChildCards = isChild && !(taskFilter === "today" && phases.length > 0);
  const visibleInstances = limitChildCards ? filteredInstances.slice(0, 5) : filteredInstances;
  const hiddenChildTaskCount = limitChildCards ? Math.max(0, filteredInstances.length - visibleInstances.length) : 0;
  const emptyCopy = isChild ? childEmptyCopy(taskFilter) : familyEmptyCopy(taskFilter);
  const todayTasks = instances.filter((instance) => instance.due_date === todayIso);
  const todayOpenCount = todayTasks.filter((instance) => instance.status === "open").length;
  const waitingCount = isParent
    ? completedInstances.length
    : instances.filter((instance) => instance.status === "completed").length;
  const todayDoneCount = todayTasks.filter((instance) => instance.status !== "open").length;

  function renderTaskCard(instance: TaskInstance, showOwner: boolean) {
    const canComplete =
      instance.status === "open" &&
      instance.due_date <= todayIso &&
      (instance.assignee_user_id === ownUserId || isParent || isDashboard);
    const canApprove = isParent && instance.status === "completed";
    const phase = phaseOf(instance, phases);
    // Today's task of a phase that has not started yet: shown, but not tickable ("ab 19:00").
    const notBefore = instance.due_date === todayIso ? notBeforeLabel(instance, phases, now) : null;
    const child = childById.get(instance.assignee_user_id);
    const dueLabel = formatDueLabel(instance.due_date, todayIso, tomorrowIso);
    const statusLabel =
      instance.status === "open" && instance.due_date > todayIso ? "Geplant" : taskStatusLabel(instance.status);
    const statusHelp =
      instance.status === "open" && instance.due_date > todayIso
        ? `Ab ${dueLabel} abhakbar.`
        : taskStatusHelp(instance.status, instance.template);
    return (
      <div
        key={instance.id}
        className={`task-board-card ${STATUS_CHIP_CLASS[instance.status]}${notBefore && canComplete ? " task-board-card--upcoming" : ""}`}
      >
        <div className={`icon-chip ${STATUS_CHIP_CLASS[instance.status]}`} aria-hidden="true">
          {taskCardIcon(instance)}
        </div>
        <div className="task-card-main">
          <div className="task-card-topline">
            {showOwner && child && (
              <span className="task-owner">
                <span className="task-owner-avatar" aria-hidden="true">
                  {child.avatar_key || "🙂"}
                </span>
                {child.display_name}
              </span>
            )}
            <span className="task-date-badge">{dueLabel}</span>
          </div>
          <div className="task-card-titleline">
            <div className="card-title">{instance.template.title}</div>
            {phase && (
              <span className="phase-chip">
                <span aria-hidden="true">{phase.icon || "🕒"}</span>
                {phase.name}
              </span>
            )}
          </div>
          <div className="task-card-pills" aria-label="Aufgabenstatus">
            <span className={`status-pill ${STATUS_CHIP_CLASS[instance.status] || "accent"}`}>
              <span aria-hidden="true">{STATUS_PILL_ICONS[instance.status]}</span>
              {statusLabel}
            </span>
            <span className="status-pill">
              <span aria-hidden="true">{KIND_ICONS[instance.template.kind]}</span>
              {taskKindLabel(instance.template)}
            </span>
          </div>
          <div className="task-reward-note" style={{ marginTop: 8 }}>
            {statusHelp}
          </div>
          {(canComplete || canApprove) && (
            <div className="task-card-actions">
              {canComplete && (
                <button disabled={Boolean(notBefore)} onClick={() => markComplete(instance.id)}>
                  <span aria-hidden="true">{notBefore ? "⏳" : "👍"}</span>
                  {notBefore ?? "Fertig!"}
                </button>
              )}
              {canApprove && (
                <button disabled={approvingId === instance.id} onClick={() => handleApproveInstance(instance.id)}>
                  <span aria-hidden="true">{approvingId === instance.id ? "⏳" : "✅"}</span>
                  {approvingId === instance.id ? "Wird freigegeben…" : "Freigeben"}
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    );
  }

  /** The cards of one list. Today's cards are grouped by day phase (heading, current phase highlighted, later ones dimmed). */
  function renderTaskCards(list: TaskInstance[], showOwner: boolean) {
    if (taskFilter !== "today" || phases.length === 0) return list.map((instance) => renderTaskCard(instance, showOwner));
    const groups = groupByPhase(list, phases, now);
    if (!groups.some((group) => group.phase)) return list.map((instance) => renderTaskCard(instance, showOwner));
    return (
      <div className="phase-groups">
        {groups.map((group) =>
          group.phase ? (
            <section
              key={group.phase.id}
              className={`phase-group phase-group--${group.state}`}
              aria-label={group.phase.name}
            >
              <h4 className="phase-heading">
                <span className="phase-heading-icon" aria-hidden="true">
                  {group.phase.icon || "🕒"}
                </span>
                <span className="phase-heading-name">{group.phase.name}</span>
                <span className="phase-heading-time">{phaseStartLabel(group.phase)}</span>
              </h4>
              <div className="task-board-list task-board-list--column">
                {group.items.map((instance) => renderTaskCard(instance, showOwner))}
              </div>
            </section>
          ) : (
            <div key="no-phase" className="task-board-list task-board-list--column">
              {group.items.map((instance) => renderTaskCard(instance, showOwner))}
            </div>
          )
        )}
      </div>
    );
  }

  return (
    <>
      <h1>{isChild ? "Meine Aufgaben" : isDashboard ? "Familien-Aufgaben" : "Aufgabenboard"}</h1>
      <p className="subtitle">
        {isChild
          ? "Such dir den nächsten Schritt aus. Fertige Aufgaben warten danach auf deine Eltern."
          : isDashboard
            ? "Alles, was am Wandtablet abgehakt werden kann."
            : "Heute im Blick, Freigaben direkt dabei."}
      </p>

      {error && <div className="error-banner">{error}</div>}

      {celebrateMessage && <div className="celebrate-banner">⭐ {celebrateMessage}</div>}

      {!isChild && (
        <div className="task-board-summary" aria-label="Aufgabenübersicht">
          <div className="task-summary-card">
            <span className="task-summary-icon" aria-hidden="true">📌</span>
            <span className="task-summary-value">{todayOpenCount}</span>
            <span className="task-summary-label">heute offen</span>
          </div>
          <div className="task-summary-card sky">
            <span className="task-summary-icon" aria-hidden="true">👍</span>
            <span className="task-summary-value">{waitingCount}</span>
            <span className="task-summary-label">wartet</span>
          </div>
          <div className="task-summary-card leaf">
            <span className="task-summary-icon" aria-hidden="true">✅</span>
            <span className="task-summary-value">{todayDoneCount}</span>
            <span className="task-summary-label">heute erledigt</span>
          </div>
        </div>
      )}

      <div className="task-filter-tabs" role="tablist" aria-label="Aufgaben filtern">
        {filterOptions.map((filter) => (
          <button
            key={filter.id}
            type="button"
            role="tab"
            aria-selected={taskFilter === filter.id}
            className={taskFilter === filter.id ? "active" : "secondary"}
            onClick={() => setTaskFilter(filter.id)}
          >
            <span aria-hidden="true" className="task-filter-icon">
              {FILTER_ICONS[filter.id]}
            </span>
            <span className="task-filter-label">{filter.label}</span>
            <span className="task-filter-count">{filter.count}</span>
          </button>
        ))}
      </div>

      {visibleInstances.length === 0 ? (
        <div className="empty-state-playful">
          <span className="emoji" aria-hidden="true">
            🎉
          </span>
          <div className="headline">{emptyCopy?.headline ?? "Alles geschafft!"}</div>
          <div className="subtext">{emptyCopy?.subtext ?? "Du hast gerade keine Aufgaben."}</div>
        </div>
      ) : !isChild && orderedChildColumns.length > 0 ? (
        <div className="task-child-columns">
          {orderedChildColumns.map((child) => {
            const childInstances = visibleInstances.filter((instance) => instance.assignee_user_id === child.id);
            return (
              <section key={child.id} className="task-child-column" aria-label={`Aufgaben von ${child.display_name}`}>
                <div className="task-child-column-header">
                  <div>
                    <span className="task-child-avatar" aria-hidden="true">
                      {child.avatar_key || "🙂"}
                    </span>
                    <span>{child.display_name}</span>
                  </div>
                  <span className="task-child-column-count">{childInstances.length}</span>
                </div>
                {childInstances.length === 0 ? (
                  <div className="task-child-column-empty">Keine Aufgaben in diesem Filter.</div>
                ) : (
                  <div className="task-board-list task-board-list--column">
                    {renderTaskCards(childInstances, false)}
                  </div>
                )}
              </section>
            );
          })}
        </div>
      ) : (
        <div className="task-board-list">
          {renderTaskCards(visibleInstances, !isChild)}
          {hiddenChildTaskCount > 0 && (
            <div className="card-meta" style={{ textAlign: "center", marginTop: -4 }}>
              Noch {hiddenChildTaskCount} Aufgabe{hiddenChildTaskCount === 1 ? "" : "n"} in diesem Filter.
            </div>
          )}
        </div>
      )}

      {isParent && (
        <details className="task-admin-panel">
          <summary>
            <span>Aufgaben verwalten</span>
            <small>Vorlagen und Zuweisungen</small>
          </summary>

          <div className="task-admin-grid">
            <DayPhasesCard onChange={setPhases} />

            <div className="form-card">
              <h3 style={{ marginTop: 0, marginBottom: 16, fontSize: "16px" }}>
                {editingTemplateId ? "Vorlage bearbeiten" : "Neue Aufgaben-Vorlage"}
              </h3>
              {editingTemplateId && templateFormError && (
                <div className="error-banner" role="alert">
                  {templateFormError}
                </div>
              )}
              <form id="template-form" onSubmit={editingTemplateId ? handleSaveTemplate : handleCreateTemplate}>
                <label htmlFor="template-title">Titel</label>
                <input
                  id="template-title"
                  type="text"
                  value={templateTitle}
                  onChange={(e) => setTemplateTitle(e.target.value)}
                  placeholder="z.B. Zimmer aufräumen"
                  required
                />

                <label>Icon</label>
                <IconPicker value={templateIcon} onChange={setTemplateIcon} />

                <label htmlFor="template-kind">Art</label>
                <select
                  id="template-kind"
                  value={templateKind}
                  onChange={(e) => {
                    setTemplateKind(e.target.value as "basis" | "bonus" | "geld");
                    if (e.target.value === "basis") {
                      setTemplatePoints("");
                    }
                  }}
                >
                  <option value="basis">Basis</option>
                  <option value="bonus">Bonus</option>
                  <option value="geld">Geld</option>
                </select>
                {templateKind === "basis" && (
                  <p className="card-meta" style={{ margin: "4px 0 12px 0" }}>
                    Basis-Aufgaben sind Pflicht und geben keine Punkte.
                  </p>
                )}

                {templateKind === "bonus" && (
                  <>
                    <label htmlFor="template-points">Punkte</label>
                    <input
                      id="template-points"
                      type="number"
                      value={templatePoints}
                      onChange={(e) => setTemplatePoints(e.target.value)}
                      placeholder="z.B. 5"
                      min="1"
                      required
                    />
                  </>
                )}

                {templateKind === "bonus" && (
                  <>
                    <label htmlFor="template-multiplier">Bonus-Multiplikator</label>
                    <input
                      id="template-multiplier"
                      type="number"
                      step="0.1"
                      value={templateMultiplier}
                      onChange={(e) => setTemplateMultiplier(e.target.value)}
                      placeholder="z.B. 1.5"
                      min="0.1"
                      max="5"
                    />
                  </>
                )}

                {templateKind === "geld" && (
                  <>
                    <label htmlFor="template-money">Betrag CHF</label>
                    <input
                      id="template-money"
                      type="number"
                      step="0.5"
                      value={templateMoney}
                      onChange={(e) => setTemplateMoney(e.target.value)}
                      placeholder="z.B. 10"
                      min="0.5"
                      required
                    />
                  </>
                )}

                {phases.length > 0 && (
                  <div className="phase-picker">
                    <label id="template-phases-label">Phasen</label>
                    {PHASE_PICKER_GROUPS.map((group) => {
                      const rows = sortPhases(phases).filter((p) => p.day_type === group.type || p.day_type === "both");
                      if (rows.length === 0) return null;
                      return (
                        <div key={group.type} className="phase-picker-group" role="group" aria-label={group.label}>
                          <span className="phase-picker-label">
                            <span aria-hidden="true">{group.icon}</span> {group.label}
                          </span>
                          <div className="breaks-chips">
                            {rows.map((p) => {
                              const on = templatePhaseIds.includes(p.id);
                              return (
                                <button
                                  key={p.id}
                                  type="button"
                                  className={`breaks-chip phase-pick${on ? " is-on" : ""}`}
                                  aria-pressed={on}
                                  onClick={() => togglePhase(p.id)}
                                >
                                  <span aria-hidden="true">{p.icon || "🕒"}</span>
                                  {p.name}
                                </button>
                              );
                            })}
                          </div>
                        </div>
                      );
                    })}
                    <p className="card-meta phase-picker-hint">Ohne Auswahl gilt die Aufgabe für den ganzen Tag.</p>
                  </div>
                )}

                <label className="carry-over-toggle" htmlFor="template-carry-over">
                  <input
                    id="template-carry-over"
                    type="checkbox"
                    checked={templateCarryOver}
                    onChange={(e) => setTemplateCarryOver(e.target.checked)}
                  />
                  <span>Verpasste Aufgaben aufsummieren</span>
                </label>
                <p className="card-meta" style={{ margin: "0 0 12px 0" }}>
                  Aus: Verpasstes verfällt, sobald die nächste fällig ist.
                </p>

                {editingTemplateId && (
                  <p className="card-meta" style={{ margin: "0 0 12px 0" }}>
                    Art und Punkte lassen sich nur ändern, solange keine Aufgabe bestätigt wurde.
                  </p>
                )}

                <div className="template-form-actions">
                  <button type="submit" disabled={creatingTemplate}>
                    {editingTemplateId
                      ? creatingTemplate
                        ? "Wird gespeichert…"
                        : "Vorlage speichern"
                      : creatingTemplate
                        ? "Wird erstellt…"
                        : "Erstellen"}
                  </button>
                  {editingTemplateId && (
                    <button type="button" className="secondary" onClick={cancelEditTemplate} disabled={creatingTemplate}>
                      Abbrechen
                    </button>
                  )}
                </div>
              </form>
            </div>

            {(templates.length > 0 || templateListMessage) && (
              <div className="form-card">
                <h3 style={{ marginTop: 0, marginBottom: 16, fontSize: "16px" }}>Vorlagen</h3>
                {templateListMessage &&
                  (templateListMessage.tone === "error" ? (
                    <div className="error-banner" role="alert">
                      {templateListMessage.text}
                    </div>
                  ) : (
                    <div className="celebrate-banner" role="status">
                      {templateListMessage.text}
                    </div>
                  ))}
                {templates.length > 0 && (
                  <ul className="template-list" aria-label="Vorlagen">
                    {templates.map((t) => (
                      <li key={t.id} className={`template-row${editingTemplateId === t.id ? " editing" : ""}`}>
                        <span className="template-row-icon" aria-hidden="true">
                          {t.icon || KIND_ICONS[t.kind]}
                        </span>
                        <span className="template-row-title">{t.title}</span>
                        {(t.phase_ids ?? []).length > 0 && (
                          <span className="template-row-phases" aria-label="Phasen">
                            {sortPhases(phases)
                              .filter((p) => (t.phase_ids ?? []).includes(p.id))
                              .map((p) => (
                                <span key={p.id} className="phase-chip phase-chip--icon" title={p.name} role="img" aria-label={p.name}>
                                  {p.icon || "🕒"}
                                </span>
                              ))}
                          </span>
                        )}
                        {t.carry_over && (
                          <span className="template-row-carry" role="img" aria-label="Verpasstes wird aufsummiert" title="Verpasstes wird aufsummiert">
                            ➕
                          </span>
                        )}
                        <span className={`status-pill ${t.kind === "basis" ? "" : t.kind === "bonus" ? "accent" : "leaf"}`}>
                          {TEMPLATE_KIND_LABELS[t.kind]}
                        </span>
                        {confirmDeleteId === t.id ? (
                          <div className="template-row-confirm">
                            <strong>Löschen?</strong>
                            <button
                              type="button"
                              onClick={() => handleDeleteTemplate(t)}
                              disabled={deletingTemplateId === t.id}
                            >
                              Ja, löschen
                            </button>
                            <button type="button" className="secondary" onClick={() => setConfirmDeleteId(null)}>
                              Abbrechen
                            </button>
                          </div>
                        ) : (
                          <div className="template-row-actions">
                            <button
                              type="button"
                              className="icon-btn"
                              aria-label={`Vorlage bearbeiten: ${t.title}`}
                              title="Bearbeiten"
                              onClick={() => startEditTemplate(t)}
                            >
                              <span aria-hidden="true">✏️</span>
                            </button>
                            <button
                              type="button"
                              className="secondary icon-btn"
                              aria-label={`Vorlage löschen: ${t.title}`}
                              title="Löschen"
                              onClick={() => {
                                setTemplateListMessage(null);
                                setConfirmDeleteId(t.id);
                              }}
                            >
                              <span aria-hidden="true">🗑️</span>
                            </button>
                          </div>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            <div className="form-card">
              <h3 style={{ marginTop: 0, marginBottom: 16, fontSize: "16px" }}>Aufgabe zuweisen</h3>
              {templates.length === 0 ? (
                <p className="empty-state" style={{ padding: "20px 12px" }}>
                  Zuerst eine Aufgaben-Vorlage anlegen.
                </p>
              ) : childAccounts.length === 0 ? (
                <p className="empty-state" style={{ padding: "20px 12px" }}>
                  Keine Kinder-Konten vorhanden.
                </p>
              ) : (
                <form onSubmit={handleAssignTask}>
                  <label htmlFor="assign-template">Aufgaben-Vorlage</label>
                  <select
                    id="assign-template"
                    value={selectedTemplate}
                    onChange={(e) => setSelectedTemplate(e.target.value)}
                    required
                  >
                    <option value="">– Bitte wählen –</option>
                    {templates.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.title}
                      </option>
                    ))}
                  </select>

                  <label>Kind(er)</label>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
                    {childAccounts.map((c) => {
                      const checked = selectedChildIds.includes(c.id);
                      return (
                        <label
                          key={c.id}
                          style={{
                            display: "inline-flex",
                            alignItems: "center",
                            gap: 6,
                            margin: 0,
                            padding: "8px 14px",
                            borderRadius: 999,
                            border: `1.5px solid ${checked ? "var(--accent)" : "var(--border)"}`,
                            background: checked ? "var(--accent-soft)" : "var(--surface)",
                            color: checked ? "var(--accent)" : "var(--text)",
                            cursor: "pointer",
                            fontSize: 14,
                            fontWeight: 500,
                          }}
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggleChild(c.id)}
                            style={{ width: "auto", margin: 0 }}
                          />
                          {c.display_name}
                        </label>
                      );
                    })}
                  </div>

                  <label htmlFor="assign-recurrence">Wiederholung</label>
                  <select
                    id="assign-recurrence"
                    value={recurrenceRule}
                    onChange={(e) => {
                      setRecurrenceRule(e.target.value as "" | "daily" | "weekly");
                      setSelectedWeekdays([]);
                    }}
                  >
                    <option value="">Einmalig</option>
                    <option value="daily">Täglich</option>
                    <option value="weekly">Wöchentlich</option>
                  </select>

                  {recurrenceRule === "weekly" && (
                    <>
                      <label>An welchen Tagen? (mehrere möglich, z.B. zweimal die Woche)</label>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
                        {WEEKDAY_LABELS.map((label, day) => {
                          const checked = selectedWeekdays.includes(day);
                          return (
                            <label
                              key={day}
                              style={{
                                display: "inline-flex",
                                alignItems: "center",
                                gap: 6,
                                margin: 0,
                                padding: "8px 14px",
                                borderRadius: 999,
                                border: `1.5px solid ${checked ? "var(--accent)" : "var(--border)"}`,
                                background: checked ? "var(--accent-soft)" : "var(--surface)",
                                color: checked ? "var(--accent)" : "var(--text)",
                                cursor: "pointer",
                                fontSize: 14,
                                fontWeight: 500,
                              }}
                            >
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => toggleWeekday(day)}
                                style={{ width: "auto", margin: 0 }}
                              />
                              {label}
                            </label>
                          );
                        })}
                      </div>
                      <p className="card-meta" style={{ margin: "-6px 0 12px 0" }}>
                        Nichts ausgewählt = einmal pro Woche, am Wochentag des Startdatums.
                      </p>
                    </>
                  )}

                  <label htmlFor="assign-due-date">{recurrenceRule ? "Startdatum" : "Fälligkeitsdatum"}</label>
                  <input
                    id="assign-due-date"
                    type="date"
                    value={dueDate}
                    onChange={(e) => setDueDate(e.target.value)}
                    required
                  />
                  {recurrenceRule && (
                    <p className="card-meta" style={{ margin: "-6px 0 12px 0" }}>
                      {recurrenceRule === "daily"
                        ? "Wird täglich ab dem Startdatum neu fällig."
                        : "Wird wöchentlich ab dem Startdatum neu fällig."}
                    </p>
                  )}

                  <button type="submit" disabled={assigningTask}>
                    {assigningTask ? "Wird zugewiesen…" : "Zuweisen"}
                  </button>
                </form>
              )}
            </div>
          </div>
        </details>
      )}
    </>
  );
}
