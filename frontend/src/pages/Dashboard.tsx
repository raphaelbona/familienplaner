import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch, decodeRoleFromToken, decodeUserIdFromToken, getFamilyId, getToken } from "../api/client";
import { UsersIcon } from "../components/dayIcons";
import DaySummaryCell from "../components/DaySummaryCell";
import FamilyMemberTile from "../components/FamilyMemberTile";
import PhasedTaskTiles from "../components/PhasedTaskTiles";
import type { TileTask } from "../components/TaskTile";
import type { DayState } from "../components/WeekStrip";
import WeekStrip from "../components/WeekStrip";
import WeatherCard from "../components/WeatherCard";
import { AVATAR_OPTIONS } from "../constants/avatars";
import { subscribeToPush, type SubscribeResult } from "../push";
import { colorForPerson } from "../utils/personColor";
import { taskRewardLabel, withoutExpired, type TaskStatus } from "../utils/taskDisplay";
import { currentWeekBounds } from "../utils/week";
import { localIsoDateOffset, toLocalIsoDate, todayIsoDate, tomorrowIsoDate } from "../utils/date";
import { breaksForChild, eventOverlapsDay, formatShortDate, summarizeDay } from "../utils/dayBar";
import { fetchSchoolBreaks, type SchoolBreak } from "../api/schoolBreaks";
import { fetchDayPhases, type DayPhase } from "../api/dayPhases";

type Child = {
  id: string;
  display_name: string;
  avatar_key: string | null;
  /** Stable position among all children of the family (server-computed), so a child has one color everywhere. */
  color_index?: number;
};

type PointsBalance = {
  balance: number;
};

type EarningsBalance = {
  pending_chf: number;
  approved_chf: number;
  paid_out_chf: number;
};

type TaskTemplate = {
  title: string;
  kind: "basis" | "bonus" | "geld";
  points_value: number;
  bonus_multiplier: number;
  money_value_chf: number;
  icon: string | null;
};

type TaskInstance = {
  id: string;
  assignee_user_id: string;
  due_date: string;
  status: TaskStatus;
  /** The day phase this instance belongs to; null = the whole day. */
  phase_id: string | null;
  template: TaskTemplate;
};

type TimetableEntry = {
  id: string;
  day_of_week: number;
  start_time: string;
  end_time: string;
  subject: string;
  room: string | null;
};

type Reward = {
  id: string;
  title: string;
  points_cost: number;
};

type MealPlanEntry = {
  id: string;
  date: string;
  meal_type: string;
  recipe_name: string;
};

type FamilyInfo = {
  name: string;
};

const MEAL_LABELS: Record<string, string> = {
  breakfast: "Frühstück",
  lunch: "Mittagessen",
  dinner: "Abendessen",
};

const MEAL_ICONS: Record<string, string> = {
  breakfast: "🍳",
  lunch: "🥪",
  dinner: "🍲",
};

/** The later of this week's Sunday and tomorrow's date — tomorrow falls into next
 * week's Monday when today is Sunday, so the weekly task query must reach that far
 * to still show a "morgen X Aufgaben" hint on the last day of the week. */
function weekQueryEnd(sunday: string): string {
  const tomorrow = tomorrowIsoDate();
  return tomorrow > sunday ? tomorrow : sunday;
}

function timeGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 11) return "Guten Morgen";
  if (hour < 18) return "Guten Tag";
  return "Guten Abend";
}

type CalendarEvent = {
  id: string;
  title: string;
  start: string;
  end: string;
  all_day: boolean;
  source: string;
  owner_user_id: string | null;
};

// The calendar also contains auto-generated entries mirroring each lesson (source
// "timetable"); the "Termine" widgets should only show genuine appointments.
function isAppointment(event: CalendarEvent): boolean {
  return event.source !== "timetable";
}

/** Python-style weekday (0=Monday..6=Sunday) of a date -- the timetable/backend convention. */
function pythonWeekday(date: Date): number {
  return (date.getDay() + 6) % 7;
}

/** Appointments touching yesterday..the day after tomorrow: enough for the "Heute und Morgen" cards, with slack for
 * a wall tablet that keeps the page open across midnight. The cards then pick their own days from this. */
function keepNearbyEvents(events: CalendarEvent[], now: Date): CalendarEvent[] {
  const days = [-1, 0, 1, 2].map((offset) => localIsoDateOffset(offset, now));
  return events.filter(isAppointment).filter((e) => days.some((day) => eventOverlapsDay(e, day)));
}

/** A compact single-line notifications row instead of a full card — on an always-visible
 * wall dashboard, a one-time permission prompt shouldn't compete for space with the
 * actual family content. Collapses to nothing once the choice has already been made. */
function CompactNotifications({
  pushStatus,
  onEnable,
}: {
  pushStatus: SubscribeResult | "loading" | null;
  onEnable: () => void;
}) {
  if (pushStatus === "subscribed" || pushStatus === "denied" || pushStatus === "unsupported" || pushStatus === "unavailable") {
    return null;
  }
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 4px", fontSize: 13 }}>
      <span aria-hidden="true">🔔</span>
      <span style={{ color: "var(--text-muted)", flex: 1 }}>Hinweis bei neuen Aufgaben erhalten?</span>
      <button
        disabled={pushStatus === "loading"}
        onClick={onEnable}
        style={{ fontSize: 12.5, padding: "6px 12px", minHeight: "auto" }}
      >
        {pushStatus === "loading" ? "…" : "Aktivieren"}
      </button>
    </div>
  );
}

/** Fixed tile order (title, then id): the backend only orders by due date, so a task would jump to the
 *  end of the list after it was ticked off and a second tap would hit a different tile. */
function byStableTileOrder(a: TaskInstance, b: TaskInstance): number {
  return a.template.title.localeCompare(b.template.title, "de") || a.id.localeCompare(b.id);
}

function toTileTask(instance: TaskInstance): TileTask {
  return {
    id: instance.id,
    title: instance.template.title,
    status: instance.status,
    icon: instance.template.icon,
    phase_id: instance.phase_id ?? null,
    template: instance.template,
  };
}

/** Per-day task-completion state ("done"/"partial"/"open"/"none") for a week strip, plus
 * today's/this week's "X von Y erledigt" fractions — computed from one person's task
 * instances for the week. */
function computeTaskProgress(allInstances: TaskInstance[], weekDays: string[], todayIso: string, tomorrowIso: string) {
  // Expired ("verfallen") tasks are history: they neither count as open nor as done.
  const instances = withoutExpired(allInstances);
  const todayInstances = instances.filter((i) => i.due_date === todayIso);
  const todayTotal = todayInstances.length;
  const todayDone = todayInstances.filter((i) => i.status !== "open").length;

  const tomorrowTotal = instances.filter((i) => i.due_date === tomorrowIso).length;

  // Week totals only count days actually within Mon-Sun, in case the caller's fetch
  // window was widened by a day to also cover "tomorrow" spilling into next week.
  const thisWeekInstances = instances.filter((i) => weekDays.includes(i.due_date));
  const weekTotal = thisWeekInstances.length;
  const weekDone = thisWeekInstances.filter((i) => i.status !== "open").length;

  const dayStates: DayState[] = weekDays.map((day) => {
    const dayInstances = instances.filter((i) => i.due_date === day);
    if (dayInstances.length === 0) return "none";
    const doneCount = dayInstances.filter((i) => i.status !== "open").length;
    if (doneCount === 0) return "open";
    if (doneCount === dayInstances.length) return "done";
    return "partial";
  });

  return { todayDone, todayTotal, tomorrowTotal, weekDone, weekTotal, dayStates };
}

export default function Dashboard() {
  const token = getToken();
  const role = token ? decodeRoleFromToken(token) : null;
  const userId = token ? decodeUserIdFromToken(token) : null;
  const isParent = role === "parent_admin" || role === "parent_member";
  const isDashboard = role === "dashboard";
  const isFamilyDashboard = isParent || isDashboard;
  const isChild = role === "child";

  // On the wall dashboard, a parent/dashboard user can tap a child to see their own
  // view, then switch back — the child never needs to log in on the shared tablet.
  const [viewingChildId, setViewingChildId] = useState<string | null>(null);
  const effectiveChildId = isChild ? userId : viewingChildId;
  const showChildDetailView = isChild || (isFamilyDashboard && viewingChildId !== null);
  const showFamilyOverview = isFamilyDashboard && viewingChildId === null;

  // The wall tablet stays open all day: tick every minute so "in 25 Min" hints and finished blocks stay current.
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  const [error, setError] = useState<string | null>(null);
  const [children, setChildren] = useState<Child[] | null>(null);
  const [childrenLoading, setChildrenLoading] = useState(false);
  const [childrenError, setChildrenError] = useState<string | null>(null);
  const [familyInfo, setFamilyInfo] = useState<FamilyInfo | null>(null);

  // Child dashboard state
  const [me, setMe] = useState<Child | null>(null);
  const [pointsBalance, setPointsBalance] = useState<PointsBalance | null>(null);
  const [earningsBalance, setEarningsBalance] = useState<EarningsBalance | null>(null);
  const [weekInstances, setWeekInstances] = useState<TaskInstance[]>([]);
  const [lessons, setLessons] = useState<TimetableEntry[]>([]);
  const [todayTomorrowEvents, setTodayTomorrowEvents] = useState<CalendarEvent[]>([]);
  const [nextReward, setNextReward] = useState<Reward | null>(null);
  const [todaysMeals, setTodaysMeals] = useState<MealPlanEntry[]>([]);
  const [tomorrowMeals, setTomorrowMeals] = useState<MealPlanEntry[]>([]);
  const [completingId, setCompletingId] = useState<string | null>(null);
  const [childTaskFeedback, setChildTaskFeedback] = useState<string | null>(null);
  const [pushStatus, setPushStatus] = useState<SubscribeResult | "loading" | null>(null);

  // Family-overview state (parent home screen)
  const [childTimetables, setChildTimetables] = useState<Record<string, TimetableEntry[]>>({});
  const [familyUpcomingEvents, setFamilyUpcomingEvents] = useState<CalendarEvent[]>([]);
  const [familyWeekInstances, setFamilyWeekInstances] = useState<TaskInstance[]>([]);
  const [pendingApprovals, setPendingApprovals] = useState<TaskInstance[]>([]);
  const [familyTodaysMeals, setFamilyTodaysMeals] = useState<MealPlanEntry[]>([]);
  const [familyTomorrowMeals, setFamilyTomorrowMeals] = useState<MealPlanEntry[]>([]);
  const [familyCompletingId, setFamilyCompletingId] = useState<string | null>(null);

  // School holidays and free days: fetched once per dashboard load (today..today+60 days) and applied per child.
  const [schoolBreaks, setSchoolBreaks] = useState<SchoolBreak[]>([]);
  useEffect(() => {
    if (!isFamilyDashboard && !isChild) return;
    fetchSchoolBreaks(localIsoDateOffset(0), localIsoDateOffset(60))
      .then((rows) => setSchoolBreaks(Array.isArray(rows) ? rows : []))
      .catch(() => setSchoolBreaks([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isFamilyDashboard, isChild]);

  // Day phases ("Morgen", "Abend", ...): today's tiles are grouped by them. A failing request just means no phases.
  const [dayPhases, setDayPhases] = useState<DayPhase[]>([]);
  useEffect(() => {
    if (!isFamilyDashboard && !isChild) return;
    fetchDayPhases()
      .then((rows) => setDayPhases(Array.isArray(rows) ? rows : []))
      .catch(() => setDayPhases([]));
  }, [isFamilyDashboard, isChild]);

  // Surface backend connectivity problems without showing raw technical status on Heute.
  useEffect(() => {
    apiFetch("/health").catch((err) => setError(err instanceof Error ? err.message : "Backend nicht erreichbar"));
  }, []);

  // Fetch children for parents and the wall dashboard
  useEffect(() => {
    if (!isFamilyDashboard) return;

    setChildrenLoading(true);
    setChildrenError(null);

    apiFetch<Child[]>("/families/children")
      .then(setChildren)
      .catch((err) =>
        setChildrenError(err instanceof Error ? err.message : "Fehler beim Laden der Kinder")
      )
      .finally(() => setChildrenLoading(false));

    apiFetch<FamilyInfo>("/families/me")
      .then(setFamilyInfo)
      .catch(() => setFamilyInfo(null));
  }, [isFamilyDashboard]);

  // Fetch each child's timetable for the "today & tomorrow" family overview
  useEffect(() => {
    if (!isFamilyDashboard || !children || children.length === 0) return;

    Promise.all(
      children.map((child) =>
        apiFetch<TimetableEntry[]>(`/timetables/${child.id}`)
          .then((entries) => [child.id, entries] as const)
          .catch(() => [child.id, []] as const)
      )
    ).then((pairs) => setChildTimetables(Object.fromEntries(pairs)));
  }, [isFamilyDashboard, children]);

  // Fetch calendar events happening today or tomorrow, for the family overview
  useEffect(() => {
    if (!isFamilyDashboard) return;

    apiFetch<CalendarEvent[]>("/calendar/events")
      .then((events) => setFamilyUpcomingEvents(keepNearbyEvents(events, new Date())))
      .catch(() => setFamilyUpcomingEvents([]));
  }, [isFamilyDashboard]);

  // Family-wide weekly task progress (for the "Familie heute" tiles) and the parent
  // confirmation queue ("wartet auf Eltern"), plus today's family meal plan.
  function loadFamilyTaskData() {
    const { monday, sunday } = currentWeekBounds();
    apiFetch<TaskInstance[]>(`/tasks/instances?due_after=${monday}&due_before=${weekQueryEnd(sunday)}`)
      .then((rows) => setFamilyWeekInstances(withoutExpired(rows)))
      .catch(() => setFamilyWeekInstances([]));

    apiFetch<TaskInstance[]>("/tasks/instances?status_filter=completed")
      .then((rows) => setPendingApprovals(withoutExpired(rows)))
      .catch(() => setPendingApprovals([]));
  }

  useEffect(() => {
    if (!isFamilyDashboard) return;

    loadFamilyTaskData();

    apiFetch<MealPlanEntry[]>("/meal-plan/entries")
      .then((entries) => {
        setFamilyTodaysMeals(entries.filter((e) => e.date === todayIsoDate()));
        setFamilyTomorrowMeals(entries.filter((e) => e.date === tomorrowIsoDate()));
      })
      .catch(() => {
        setFamilyTodaysMeals([]);
        setFamilyTomorrowMeals([]);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isFamilyDashboard]);

  /** Wall tile tap: open -> "ich bin fertig", completed -> undo. Approved tiles are never tappable. */
  async function toggleFamilyTask(id: string, status: TaskStatus) {
    if (status === "approved" || status === "expired") return;
    const action = status === "open" ? "complete" : "reopen";
    setFamilyCompletingId(id);
    try {
      await apiFetch(`/tasks/instances/${id}/${action}`, { method: "POST" });
      loadFamilyTaskData();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : action === "complete"
            ? "Konnte nicht als erledigt markiert werden"
            : "Konnte nicht zurückgenommen werden"
      );
    } finally {
      setFamilyCompletingId(null);
    }
  }

  function loadChildDashboard(targetUserId: string) {
    const familyId = getFamilyId();
    if (familyId) {
      apiFetch<Child[]>(`/families/${familyId}/child-accounts`)
        .then((accounts) => setMe(accounts.find((a) => a.id === targetUserId) ?? null))
        .catch(() => setMe(null));
    }

    apiFetch<EarningsBalance>(`/earnings/balance?user_id=${targetUserId}`)
      .then(setEarningsBalance)
      .catch(() => setEarningsBalance(null));

    Promise.all([apiFetch<PointsBalance>(`/points/balance?user_id=${targetUserId}`), apiFetch<Reward[]>("/rewards")])
      .then(([balanceData, rewards]) => {
        setPointsBalance(balanceData);
        if (rewards.length === 0) {
          setNextReward(null);
          return;
        }
        const sorted = [...rewards].sort((a, b) => a.points_cost - b.points_cost);
        const affordableNext = sorted.find((r) => r.points_cost > balanceData.balance);
        setNextReward(affordableNext ?? sorted[0]);
      })
      .catch(() => {
        setPointsBalance(null);
        setNextReward(null);
      });

    apiFetch<MealPlanEntry[]>("/meal-plan/entries")
      .then((entries) => {
        setTodaysMeals(entries.filter((e) => e.date === todayIsoDate()));
        setTomorrowMeals(entries.filter((e) => e.date === tomorrowIsoDate()));
      })
      .catch(() => {
        setTodaysMeals([]);
        setTomorrowMeals([]);
      });

    const { monday, sunday } = currentWeekBounds();
    apiFetch<TaskInstance[]>(
      `/tasks/instances?assignee_user_id=${targetUserId}&due_after=${monday}&due_before=${weekQueryEnd(sunday)}`
    )
      .then((rows) => setWeekInstances(withoutExpired(rows)))
      .catch((err) => setError(err instanceof Error ? err.message : "Aufgaben konnten nicht geladen werden"));

    apiFetch<TimetableEntry[]>(`/timetables/${targetUserId}`)
      .then(setLessons)
      .catch(() => setLessons([]));

    apiFetch<CalendarEvent[]>("/calendar/events")
      .then((events) =>
        setTodayTomorrowEvents(
          keepNearbyEvents(events, new Date()).filter((e) => e.owner_user_id === null || e.owner_user_id === targetUserId)
        )
      )
      .catch(() => setTodayTomorrowEvents([]));
  }

  useEffect(() => {
    if (effectiveChildId) loadChildDashboard(effectiveChildId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveChildId]);

  /** Child tile tap: open -> "ich bin fertig" (with celebration), completed -> undo (no celebration). */
  async function toggleChildTask(id: string, status: TaskStatus) {
    if (status === "approved" || status === "expired") return;
    const completing = status === "open";
    const finishedTask = weekInstances.find((task) => task.id === id);
    setCompletingId(id);
    try {
      await apiFetch(`/tasks/instances/${id}/${completing ? "complete" : "reopen"}`, { method: "POST" });
      if (effectiveChildId) loadChildDashboard(effectiveChildId);
      // Keep the wall-dashboard tiles in sync too, in case this was changed from a
      // child's detail view reached by tapping their tile — "back" must show current data.
      if (isFamilyDashboard) loadFamilyTaskData();
      if (completing) {
        const name = me?.display_name ? `, ${me.display_name}` : "";
        const rewardText =
          finishedTask && finishedTask.template.kind !== "basis"
            ? `${taskRewardLabel(finishedTask.template)}. Wartet jetzt auf Bestätigung.`
            : "Wartet jetzt auf Bestätigung.";
        setChildTaskFeedback(`Gut gemacht${name}! ${rewardText}`);
        window.setTimeout(() => setChildTaskFeedback(null), 3500);
      } else {
        setChildTaskFeedback(null);
      }
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : completing
            ? "Konnte nicht als erledigt markiert werden"
            : "Konnte nicht zurückgenommen werden"
      );
    } finally {
      setCompletingId(null);
    }
  }

  async function handleEnablePush() {
    setPushStatus("loading");
    try {
      const result = await subscribeToPush();
      setPushStatus(result);
    } catch {
      setPushStatus("unavailable");
    }
  }

  const hasEarnings =
    earningsBalance &&
    (earningsBalance.pending_chf > 0 || earningsBalance.approved_chf > 0 || earningsBalance.paid_out_chf > 0);

  const todayIso = toLocalIsoDate(now);
  const tomorrowIso = localIsoDateOffset(1, now);
  const { days: weekDays } = currentWeekBounds();
  const todayWeekday = pythonWeekday(now);
  const tomorrowWeekday = (todayWeekday + 1) % 7;
  const todayHeading = formatShortDate(now);
  const tomorrowHeading = formatShortDate(new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1));

  /** The two day summaries (today, tomorrow) of one person or of the family. */
  function summarizeTwoDays(personLessons: TimetableEntry[], personEvents: CalendarEvent[], childId?: string | null) {
    const breaks = childId ? breaksForChild(schoolBreaks, childId) : [];
    const timetableDays = personLessons.map((e) => e.day_of_week);
    return [
      summarizeDay({
        lessons: personLessons.filter((e) => e.day_of_week === todayWeekday),
        events: personEvents,
        date: todayIso,
        now,
        breaks,
        timetableDays,
      }),
      summarizeDay({
        lessons: personLessons.filter((e) => e.day_of_week === tomorrowWeekday),
        events: personEvents,
        date: tomorrowIso,
        now,
        breaks,
        timetableDays,
      }),
    ] as const;
  }

  // Only what is doable today: the child's tiles show open, done and approved tasks of today.
  const todaysTasks = withoutExpired(weekInstances)
    .filter((t) => t.due_date === todayIso)
    .sort(byStableTileOrder);
  const childProgress = computeTaskProgress(weekInstances, weekDays, todayIso, tomorrowIso);
  const currentPoints = pointsBalance?.balance ?? 0;
  const nextRewardProgress = nextReward
    ? Math.min(100, Math.round((currentPoints / nextReward.points_cost) * 100))
    : 0;
  const nextRewardRemaining = nextReward ? Math.max(0, nextReward.points_cost - currentPoints) : 0;

  // Child's own "Heute und Morgen": own lessons + appointments assigned to them or the whole family.
  const [childToday, childTomorrow] = summarizeTwoDays(lessons, todayTomorrowEvents, effectiveChildId);
  const childNamePrefix = me?.display_name ? `${me.display_name}, ` : "";

  // The child's own person color: the same one the wall dashboard shows for them.
  const meColor = colorForPerson(me?.color_index ?? 0);

  // Family overview: one row per child (own appointments only) plus a "Familie" row for ownerless ones.
  const familyDays = summarizeTwoDays([], familyUpcomingEvents.filter((e) => e.owner_user_id === null));

  return (
    <>
      {showChildDetailView ? (
        <div className="hero hero--slim">
          {!isChild && (
            <button type="button" className="secondary hero-back" onClick={() => setViewingChildId(null)}>
              ← Zurück zum Wand-Dashboard
            </button>
          )}
          <div
            className="hero-avatar"
            aria-hidden="true"
            data-testid="hero-avatar"
            style={{ background: `${meColor}22`, border: `2.5px solid ${meColor}` }}
          >
            {me?.avatar_key ?? "🙂"}
          </div>
          <h1>Hallo, {me?.display_name ?? "du"}!</h1>
          <p className="subtitle">Das ist heute für dich los.</p>
        </div>
      ) : isParent ? (
        <div className="hero hero--slim">
          <h1>
            {timeGreeting()}
            {familyInfo ? `, Familie ${familyInfo.name}` : ""}! 👋
          </h1>
          <p className="subtitle">So läuft der Tag bei euch.</p>
        </div>
      ) : isDashboard ? (
        <div className="hero hero--slim">
          <h1>Familien-Dashboard</h1>
          <p className="subtitle">Alles im Blick fürs Wandtablet.</p>
          <div className="hero-clock" data-testid="wall-clock">
            <time dateTime={now.toISOString()}>
              {String(now.getHours()).padStart(2, "0")}:{String(now.getMinutes()).padStart(2, "0")}
            </time>
            <span className="hero-clock-date">{formatShortDate(now)}</span>
          </div>
        </div>
      ) : (
        <div className="hero hero--slim">
          <h1>Willkommen zuhause 👋</h1>
          <p className="subtitle">Der schnelle Überblick über Kalender, Essen und Aufgaben.</p>
        </div>
      )}

      {error && <div className="error-banner">Backend nicht erreichbar: {error}</div>}
      {childrenError && <div className="error-banner">Fehler beim Laden: {childrenError}</div>}
      {childTaskFeedback && <div className="celebrate-banner">{childTaskFeedback}</div>}

      {showFamilyOverview && pendingApprovals.length > 0 && (
        <div className="card" style={{ marginBottom: 14 }}>
          <div className="icon-chip sky" aria-hidden="true">
            ⏳
          </div>
          <div className="card-body">
            <div className="card-title">
              {pendingApprovals.length === 1
                ? "1 Aufgabe wartet auf deine Bestätigung"
                : `${pendingApprovals.length} Aufgaben warten auf deine Bestätigung`}
            </div>
            {isParent ? (
              <Link to="/aufgaben" style={{ fontSize: "13.5px" }}>
                Jetzt bestätigen →
              </Link>
            ) : (
              <div className="card-meta">Mama oder Papa kann sie später freigeben.</div>
            )}
          </div>
        </div>
      )}

      {showFamilyOverview && (
        <div className="card" style={{ marginBottom: 14, display: "block" }}>
          <div className="card-title" style={{ marginBottom: 10 }}>
            Familie heute
          </div>
          {childrenLoading ? (
            <div className="card-meta">wird geladen…</div>
          ) : children && children.length > 0 ? (
            <>
              <div className="family-grid">
                {children.map((child, index) => {
                  const instances = familyWeekInstances.filter((i) => i.assignee_user_id === child.id);
                  const progress = computeTaskProgress(instances, weekDays, todayIso, tomorrowIso);
                  const todayTasks = instances
                    .filter((i) => i.due_date === todayIso)
                    .sort(byStableTileOrder)
                    .map(toTileTask);
                  return (
                    <FamilyMemberTile
                      key={child.id}
                      avatar={child.avatar_key || AVATAR_OPTIONS[0]}
                      name={child.display_name}
                      color={colorForPerson(child.color_index ?? index)}
                      todayDone={progress.todayDone}
                      todayTotal={progress.todayTotal}
                      todayTasks={todayTasks}
                      weekDays={progress.dayStates}
                      todayIndex={todayWeekday}
                      onToggleTask={toggleFamilyTask}
                      completingTaskId={familyCompletingId}
                      phases={dayPhases}
                      now={now}
                      onSelect={() => setViewingChildId(child.id)}
                    />
                  );
                })}
              </div>
              {isParent && (
                <Link to="/kinder" style={{ fontSize: "13.5px", display: "inline-block", marginTop: 12 }}>
                  Kinder verwalten →
                </Link>
              )}
            </>
          ) : (
            <>
              <div style={{ fontFamily: "'Baloo 2', sans-serif", fontWeight: 700, fontSize: 16, marginBottom: 4 }}>
                Kinder hinzufügen
              </div>
              <div className="card-meta">
                {isParent
                  ? "Lege jetzt die Konten deiner Kinder an, damit sie Aufgaben, Kalender und mehr nutzen können."
                  : "Noch keine Kinder für dieses Familien-Dashboard eingerichtet."}
              </div>
              {isParent && (
                <Link to="/kinder" className="btn" style={{ marginTop: "12px", display: "inline-block", textDecoration: "none" }}>
                  Zu Kinder-Verwaltung
                </Link>
              )}
            </>
          )}
        </div>
      )}

      <div className="widget-grid">

      {showChildDetailView && (
        <>
          <div className="card task-focus-card">
            <div className="icon-chip" aria-hidden="true">
              📋
            </div>
            <div className="card-body">
              <div className="card-title">Heute zu tun</div>
              {todaysTasks.length === 0 ? (
                <div className="card-meta">
                  <span aria-hidden="true">🌴</span> Heute frei
                </div>
              ) : (
                <PhasedTaskTiles
                  tasks={todaysTasks.map(toTileTask)}
                  phases={dayPhases}
                  now={now}
                  onToggle={toggleChildTask}
                  busyTaskId={completingId}
                />
              )}
              <Link to="/aufgaben" style={{ fontSize: "13.5px", display: "inline-block", marginTop: 10 }}>
                Alle Aufgaben ansehen →
              </Link>
            </div>
          </div>

          <div className="card">
            <div className="icon-chip leaf" aria-hidden="true">
              📆
            </div>
            <div className="card-body">
              <div className="card-title">Meine Woche</div>
              <div className="card-meta">
                {childProgress.weekTotal > 0
                  ? `${childProgress.weekDone}/${childProgress.weekTotal} erledigt`
                  : "Nichts geplant"}
              </div>
              <WeekStrip days={childProgress.dayStates} todayIndex={todayWeekday} color={meColor} />
            </div>
          </div>

          <div className="card">
            <div className="points-badge">
              <span className="value">{pointsBalance ? pointsBalance.balance : "…"}</span>
              <span className="label">Punkte</span>
            </div>
            <div className="card-body">
              <div className="card-title">Meine Punkte</div>
              <Link to="/belohnungen" style={{ fontSize: "13.5px" }}>
                Belohnung holen →
              </Link>
            </div>
          </div>

          {nextReward && (
            <div className="card">
              <div className="icon-chip" aria-hidden="true">
                🎁
              </div>
              <div className="card-body">
                <div className="card-title">Nächste Belohnung</div>
                <div className="card-meta">
                  {nextReward.title}
                </div>
                <div className="card-meta">
                  Du hast {currentPoints} von {nextReward.points_cost} Punkten.
                </div>
                <div className="card-meta">
                  {nextRewardRemaining === 0
                    ? "Du kannst sie einlösen."
                    : `Noch ${nextRewardRemaining} Punkte bis ${nextReward.title}.`}
                </div>
                <div className="progress-track" aria-hidden="true">
                  <div
                    className="progress-fill"
                    style={{
                      width: `${nextRewardProgress}%`,
                    }}
                  />
                </div>
                <Link to="/belohnungen" style={{ fontSize: "13.5px" }}>
                  Zu den Belohnungen →
                </Link>
              </div>
            </div>
          )}

          <div className="card">
            <div className="icon-chip sky" aria-hidden="true">
              📅
            </div>
            <div className="card-body">
              <div className="card-title">Heute und Morgen</div>
              <section className="day-section">
                <h3 className="day-pill day-pill--today">
                  Heute <span className="day-pill-date">{todayHeading}</span>
                </h3>
                <DaySummaryCell summary={childToday} label={`${childNamePrefix}Heute`} dayName="Heute" />
              </section>
              <section className="day-section">
                <h3 className="day-pill day-pill--tomorrow">
                  Morgen <span className="day-pill-date">{tomorrowHeading}</span>
                </h3>
                <DaySummaryCell summary={childTomorrow} label={`${childNamePrefix}Morgen`} dayName="Morgen" />
              </section>
              <div style={{ display: "flex", gap: 14, marginTop: 10 }}>
                <Link to="/stundenplan" style={{ fontSize: "13.5px" }}>
                  Stundenplan →
                </Link>
                <Link to="/kalender" style={{ fontSize: "13.5px" }}>
                  Kalender →
                </Link>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="icon-chip leaf" aria-hidden="true">
              🍽️
            </div>
            <div className="card-body">
              <div className="card-title">Essen</div>
              {todaysMeals.length > 0 ? (
                todaysMeals.map((meal) => (
                  <div key={meal.id} className="card-meta" style={{ marginTop: 4 }}>
                    {MEAL_ICONS[meal.meal_type] ?? "🍽️"} Heute {MEAL_LABELS[meal.meal_type] ?? meal.meal_type}: {meal.recipe_name}
                  </div>
                ))
              ) : tomorrowMeals.length > 0 ? (
                tomorrowMeals.map((meal) => (
                  <div key={meal.id} className="card-meta" style={{ marginTop: 4 }}>
                    {MEAL_ICONS[meal.meal_type] ?? "🍽️"} Morgen {MEAL_LABELS[meal.meal_type] ?? meal.meal_type}: {meal.recipe_name}
                  </div>
                ))
              ) : (
                <div className="card-meta">Heute kein Essen geplant</div>
              )}
            </div>
          </div>

          {hasEarnings && (
            <div className="card">
              <div className="icon-chip leaf" aria-hidden="true">
                💰
              </div>
              <div className="card-body">
                <div className="card-title">Mein Taschengeld</div>
                <div style={{ fontSize: "24px", fontWeight: 700, color: "var(--accent)", marginTop: 6 }}>
                  CHF {earningsBalance!.approved_chf.toFixed(2)}
                </div>
                {earningsBalance!.pending_chf > 0 && (
                  <div className="card-meta">CHF {earningsBalance!.pending_chf.toFixed(2)} wartet auf Freigabe</div>
                )}
                <Link to="/verdienst" style={{ fontSize: "13.5px" }}>
                  Zum Taschengeld →
                </Link>
              </div>
            </div>
          )}

          {token && <WeatherCard />}

          {token && <CompactNotifications pushStatus={pushStatus} onEnable={handleEnablePush} />}
        </>
      )}

      {showFamilyOverview && children && children.length > 0 && (
        <div className="card day-card">
          <div className="icon-chip sky" aria-hidden="true">
            📅
          </div>
          <div className="card-body">
            <div className="card-title">Heute und Morgen</div>
            <table className="day-table" aria-label="Heute und Morgen">
              <thead>
                <tr>
                  <th scope="col" className="day-table-corner">
                    <span className="day-sr">Person</span>
                  </th>
                  <th scope="col">
                    <span className="day-pill day-pill--today">Heute · {todayHeading}</span>
                  </th>
                  <th scope="col">
                    <span className="day-pill day-pill--tomorrow">Morgen · {tomorrowHeading}</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {children.map((child, index) => {
                  const color = colorForPerson(child.color_index ?? index);
                  const [today, tomorrow] = summarizeTwoDays(
                    childTimetables[child.id] ?? [],
                    familyUpcomingEvents.filter((e) => e.owner_user_id === child.id),
                    child.id
                  );
                  return (
                    <tr key={child.id}>
                      <th scope="row" className="day-rowhead">
                        <span
                          aria-hidden="true"
                          className="day-avatar"
                          style={{ background: `${color}22`, borderColor: color }}
                        >
                          {child.avatar_key || AVATAR_OPTIONS[0]}
                        </span>
                        <span className="day-rowname">{child.display_name}</span>
                      </th>
                      <td>
                        <DaySummaryCell summary={today} label={`${child.display_name}, Heute`} dayName="Heute" />
                      </td>
                      <td>
                        <DaySummaryCell summary={tomorrow} label={`${child.display_name}, Morgen`} dayName="Morgen" />
                      </td>
                    </tr>
                  );
                })}
                <tr>
                  <th scope="row" className="day-rowhead">
                    <span aria-hidden="true" className="day-avatar day-avatar--family">
                      <UsersIcon size={18} />
                    </span>
                    <span className="day-rowname">Familie</span>
                  </th>
                  {familyDays.map((summary, i) => (
                    <td key={i}>
                      <DaySummaryCell
                        summary={summary}
                        label={`Familie, ${i === 0 ? "Heute" : "Morgen"}`}
                        dayName={i === 0 ? "Heute" : "Morgen"}
                        emptyVariant="family"
                      />
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
            {isParent && (
              <div style={{ display: "flex", gap: 14, marginTop: 10 }}>
                <Link to="/stundenplan" style={{ fontSize: "13.5px" }}>
                  Stundenpläne verwalten →
                </Link>
                <Link to="/kalender" style={{ fontSize: "13.5px" }}>
                  Zum Kalender →
                </Link>
              </div>
            )}
          </div>
        </div>
      )}

      {showFamilyOverview && (
        <div className="card">
          <div className="icon-chip leaf" aria-hidden="true">
            🍽️
          </div>
          <div className="card-body">
            <div className="card-title">Essen</div>
            {familyTodaysMeals.length > 0 ? (
              familyTodaysMeals.map((meal) => (
                <div key={meal.id} className="card-meta" style={{ marginTop: 4 }}>
                  {MEAL_ICONS[meal.meal_type] ?? "🍽️"} Heute {MEAL_LABELS[meal.meal_type] ?? meal.meal_type}: {meal.recipe_name}
                </div>
              ))
            ) : familyTomorrowMeals.length > 0 ? (
              familyTomorrowMeals.map((meal) => (
                <div key={meal.id} className="card-meta" style={{ marginTop: 4 }}>
                  {MEAL_ICONS[meal.meal_type] ?? "🍽️"} Morgen {MEAL_LABELS[meal.meal_type] ?? meal.meal_type}: {meal.recipe_name}
                </div>
              ))
            ) : (
              <div className="card-meta">Heute kein Essen geplant</div>
            )}
          </div>
        </div>
      )}

      {showFamilyOverview && isParent && (
        <>
          {token && <WeatherCard />}
          {token && <CompactNotifications pushStatus={pushStatus} onEnable={handleEnablePush} />}
        </>
      )}

      {showFamilyOverview && isDashboard && token && <WeatherCard />}

      {!isParent && !isChild && !isDashboard && (
        <>
          {token && <WeatherCard />}
          <div className="card">
            <div className="icon-chip leaf" aria-hidden="true">
              🌱
            </div>
            <div className="card-body">
              <div className="card-title">Nächste Schritte</div>
              <div className="card-meta">
                Erkunde <strong>Kalender</strong>, <strong>Essen</strong> und <strong>Aufgaben</strong> im Menü unten.
              </div>
            </div>
          </div>
        </>
      )}
      </div>
    </>
  );
}
