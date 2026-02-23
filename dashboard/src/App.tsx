import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ChatInput } from './components/ChatInput';
import { Orb } from './components/Orb';
import { AnimatedNumber } from './components/AnimatedNumber';
import { SpotlightCard } from './components/SpotlightCard';
import { ConnectionLines } from './components/ConnectionLines';
import { BusinessOverviewPanel } from './components/BusinessOverviewPanel';
import type {
  SystemMetrics,
  RecurrenceFreq,
  RecurrenceRuleV1,
  RecurrenceWeekday,
  TaskTemplateDetailItem,
  TaskSummaryItem,
  UpcomingTaskItem,
  UpcomingTasksOverview,
  ProjectOverviewItem,
  TaskDetailItem,
  BusinessOverviewMetrics,
  ObjectiveOverviewMetrics,
  OrchestratorMetricsDashboard,
  ObjectiveSummaryItem,
  ObjectiveDetailItem,
} from './types';

interface SendMessageResult {
  text: string;
  requestId?: string;
  queued?: boolean;
  sessionId?: string;
}

interface RequestCompletionMessage {
  id: string;
  requestId: string;
  text: string;
  status: 'completed' | 'failed';
}

type TaskLane = 'recent' | 'awaiting' | 'scheduled';
type TaskCardItem = TaskSummaryItem | UpcomingTaskItem;

interface TemplateEditFormState {
  title: string;
  description: string;
  priority: number;
  timezone: string;
  isActive: boolean;
  freq: RecurrenceFreq;
  interval: number;
  byWeekday: RecurrenceWeekday[];
  anchorTime: string;
}

type PendingConfirmAction =
  | {
      kind: 'cancel-task';
      taskId: string;
      taskTitle: string;
    }
  | {
      kind: 'cancel-next-template-occurrence';
      templateId: string;
      templateTitle: string;
      nextRunAt?: number;
    }
  | {
      kind: 'deactivate-template-recurrence';
      templateId: string;
      templateTitle: string;
    };


interface ImprovementStatus {
  improvementLoop: {
    isRunning: boolean;
    cycleCount?: number;
    consecutiveNoImprovement: number;
    lastRunAt?: string | null;
    lastScoreDelta?: number | null;
    lastCycleTokens?: number | null;
    tokensLastHour?: number;
    tokenBudget: number;
    nextRunAt?: string | null;
    intervalMinutes?: number;
  };
  autonomousLoop: {
    isRunning: boolean;
    currentObjective?: string | null;
    projectsWithObjective: number;
    activeScheduledItems: number;
    lastTickAt?: string | null;
    tickMinutes?: number;
  };
  behavioralSuite: {
    lastScore: number;
    casesPassed: number;
    casesTotal: number;
  };
  providers: {
    name: string;
    state: 'CLOSED' | 'OPEN' | 'HALF_OPEN';
    failureCount: number;
  }[];
}

const UI_CLOCK_TICK_MS = 30_000;
const RESILIENCE_POLL_MS = 60_000;
const SSE_HEALTHCHECK_MS = 15_000;
const SSE_STALE_MS = 60_000;
const AGENT_LIST_INTRO_SCROLL_UNLOCK_MS = 280;
const WEEKDAY_ORDER: RecurrenceWeekday[] = [
  'monday',
  'tuesday',
  'wednesday',
  'thursday',
  'friday',
  'saturday',
  'sunday',
];
const WEEKDAY_OPTIONS: Array<{ value: RecurrenceWeekday; label: string }> = [
  { value: 'monday', label: 'Mon' },
  { value: 'tuesday', label: 'Tue' },
  { value: 'wednesday', label: 'Wed' },
  { value: 'thursday', label: 'Thu' },
  { value: 'friday', label: 'Fri' },
  { value: 'saturday', label: 'Sat' },
  { value: 'sunday', label: 'Sun' },
];

function sortWeekdays(values: RecurrenceWeekday[]): RecurrenceWeekday[] {
  const dedup = Array.from(new Set(values));
  return dedup.sort((a, b) => WEEKDAY_ORDER.indexOf(a) - WEEKDAY_ORDER.indexOf(b));
}

function extractAnchorTime(anchorLocal: string): string {
  const match = (anchorLocal || '').trim().match(/^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})$/);
  if (!match) return '09:00';
  return `${match[2]}:${match[3]}`;
}

function applyAnchorTime(anchorLocal: string, timeValue: string): string {
  const datePart = (anchorLocal || '').trim().slice(0, 10);
  const safeDate = /^\d{4}-\d{2}-\d{2}$/.test(datePart) ? datePart : new Date().toISOString().slice(0, 10);
  const timeMatch = (timeValue || '').trim().match(/^(\d{2}):(\d{2})$/);
  const safeHour = timeMatch ? Number(timeMatch[1]) : 9;
  const safeMinute = timeMatch ? Number(timeMatch[2]) : 0;
  const hh = String(Math.min(23, Math.max(0, safeHour))).padStart(2, '0');
  const mm = String(Math.min(59, Math.max(0, safeMinute))).padStart(2, '0');
  return `${safeDate}T${hh}:${mm}:00`;
}

function buildTemplateEditForm(template: TaskTemplateDetailItem): TemplateEditFormState {
  return {
    title: template.title,
    description: template.description,
    priority: template.priority,
    timezone: template.timezone,
    isActive: template.isActive,
    freq: template.recurrenceRule.schedule.freq,
    interval: template.recurrenceRule.schedule.interval,
    byWeekday: sortWeekdays(template.recurrenceRule.schedule.by_weekday || []),
    anchorTime: extractAnchorTime(template.recurrenceRule.anchor_local),
  };
}

function buildTemplateRecurrenceRule(
  template: TaskTemplateDetailItem,
  form: TemplateEditFormState
): RecurrenceRuleV1 {
  return {
    ...template.recurrenceRule,
    timezone: (form.timezone || '').trim() || template.recurrenceRule.timezone,
    anchor_local: applyAnchorTime(template.recurrenceRule.anchor_local, form.anchorTime),
    schedule: {
      ...template.recurrenceRule.schedule,
      freq: form.freq,
      interval: Math.max(1, Math.floor(form.interval || 1)),
      by_weekday: form.freq === 'weekly' ? sortWeekdays(form.byWeekday) : template.recurrenceRule.schedule.by_weekday,
    },
  };
}

function formatTaskStatusLabel(status: string): string {
  switch (status) {
    case 'running':
      return 'Running';
    case 'completed':
      return 'Completed';
    case 'failed':
      return 'Failed';
    case 'blocked':
      return 'Blocked';
    case 'canceled':
      return 'Canceled';
    case 'pending':
      return 'Queued';
    default:
      return status;
  }
}

function taskStatusClass(status: string): string {
  switch (status) {
    case 'running':
      return 'status-running';
    case 'completed':
      return 'status-completed';
    case 'failed':
      return 'status-failed';
    case 'blocked':
      return 'status-blocked';
    case 'canceled':
      return 'status-canceled';
    case 'pending':
      return 'status-pending';
    default:
      return 'status-unknown';
  }
}

function formatRelativeAge(epochSeconds?: number, nowMs = Date.now()): string {
  if (!epochSeconds || !Number.isFinite(epochSeconds)) return 'just now';
  const delta = Math.max(0, Math.floor(nowMs / 1000) - epochSeconds);
  if (delta < 60) return 'just now';
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

function formatAbsoluteDateTime(epochSeconds?: number): string {
  if (!epochSeconds || !Number.isFinite(epochSeconds)) return 'N/A';
  const date = new Date(epochSeconds * 1000);
  return new Intl.DateTimeFormat('en-US', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}

function formatDurationMs(durationMs?: number): string {
  if (!durationMs || !Number.isFinite(durationMs) || durationMs <= 0) return 'N/A';
  if (durationMs < 1000) return `${Math.round(durationMs)}ms`;
  const totalSeconds = Math.round(durationMs / 1000);
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) return `${minutes}m ${seconds}s`;
  const hours = Math.floor(minutes / 60);
  const remMinutes = minutes % 60;
  return `${hours}h ${remMinutes}m`;
}

function TaskCard({
  task,
  lane,
  processingAgentLabel,
  nowMs,
  onClick,
}: {
  task: TaskCardItem;
  lane: TaskLane;
  processingAgentLabel?: string;
  nowMs: number;
  onClick?: () => void;
}) {
  const visualLane = lane === 'recent' ? 'processing' : 'awaiting';
  const statusClass =
    lane === 'recent' ? taskStatusClass(task.status) : lane === 'scheduled' ? 'status-scheduled' : 'status-pending';
  const spotlightColor =
    lane === 'recent'
      ? 'rgba(99, 102, 241, 0.16)'
      : lane === 'scheduled'
        ? 'rgba(59, 130, 246, 0.14)'
        : 'rgba(245, 158, 11, 0.14)';
  const isScheduledTemplate = lane === 'scheduled' && 'kind' in task && task.kind === 'template';
  const recentMeta =
    task.status === 'running'
      ? processingAgentLabel || (task.agentName ? `@${task.agentName}` : 'No agent assigned')
      : task.agentName
        ? `@${task.agentName}`
        : `Priority ${task.priority}`;
  const scheduledMeta = task.dueAt
    ? `${isScheduledTemplate ? 'Next run' : 'Due'} ${formatAbsoluteDateTime(task.dueAt)}`
    : `Updated ${formatRelativeAge(task.updatedAt, nowMs)}`;
  const primaryMeta =
    lane === 'recent'
      ? recentMeta
      : lane === 'scheduled'
        ? isScheduledTemplate
          ? null
          : `Priority ${task.priority}`
        : `Priority ${task.priority}`;
  const secondaryMeta = lane === 'scheduled' ? scheduledMeta : `Updated ${formatRelativeAge(task.updatedAt, nowMs)}`;

  return (
    <SpotlightCard
      className={`task-card ${visualLane} ${statusClass} ${onClick ? 'is-clickable' : 'is-static'}`}
      activeColor={spotlightColor}
      onClick={onClick}
      includeBaseClass={false}
    >
      <div className="task-card-accent" />
      <div className="task-card-content">
        <div className="task-card-top">
          <span className={`task-state-chip ${visualLane} ${statusClass}`}>
            {lane === 'recent'
              ? formatTaskStatusLabel(task.status)
              : lane === 'scheduled'
                ? isScheduledTemplate
                  ? 'Recurring'
                  : 'Scheduled'
                : 'Queued'}
          </span>
          <span className="task-project-name">{task.projectName || 'General'}</span>
        </div>

        <p className="task-title">{task.title}</p>

        <div className="task-card-meta">
          {primaryMeta ? <span>{primaryMeta}</span> : null}
          {primaryMeta ? <span className="task-meta-divider" /> : null}
          <span>{secondaryMeta}</span>
        </div>
      </div>
    </SpotlightCard>
  );
}

const taskItemMotionTransition = {
  opacity: { duration: 0.18, ease: 'easeOut' as const },
  y: { duration: 0.22, ease: 'easeOut' as const },
  layout: {
    type: 'spring' as const,
    stiffness: 420,
    damping: 36,
    mass: 0.78,
  },
};

function App() {
  const dsUrl = 'http://localhost:8765';
  const [dashboardMode, setDashboardMode] = useState<'business' | 'live'>('live');

  const [metrics, setMetrics] = useState<SystemMetrics | null>(null);
  const [businessMetrics, setBusinessMetrics] = useState<BusinessOverviewMetrics | null>(null);
  const [objectiveOverview, setObjectiveOverview] = useState<ObjectiveOverviewMetrics | null>(null);
  const [orchestratorMetrics, setOrchestratorMetrics] = useState<OrchestratorMetricsDashboard | null>(null);
  const [objectives, setObjectives] = useState<ObjectiveSummaryItem[]>([]);
  const [selectedObjectiveId, setSelectedObjectiveId] = useState<string | null>(null);
  const [selectedObjectiveDetail, setSelectedObjectiveDetail] = useState<ObjectiveDetailItem | null>(null);
  const [isObjectiveDetailLoading, setIsObjectiveDetailLoading] = useState(false);
  const [objectiveRefreshTick, setObjectiveRefreshTick] = useState(0);
  const [improvementStatus, setImprovementStatus] = useState<ImprovementStatus | null>(null);
  const [recentTasks, setRecentTasks] = useState<TaskSummaryItem[]>([]);
  const [upcomingTasks, setUpcomingTasks] = useState<UpcomingTasksOverview>({ awaitingNow: [], scheduled: [] });
  const [projects, setProjects] = useState<ProjectOverviewItem[]>([]);
  const [processingAgentSnapshotByTaskId, setProcessingAgentSnapshotByTaskId] = useState<Record<string, string>>({});
  const [requestCompletions, setRequestCompletions] = useState<RequestCompletionMessage[]>([]);
  const [isAgentListScrollUnlocked, setIsAgentListScrollUnlocked] = useState(false);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [isTaskDetailOpen, setIsTaskDetailOpen] = useState(false);
  const [isTaskDetailLoading, setIsTaskDetailLoading] = useState(false);
  const [isTaskDetailCanceling, setIsTaskDetailCanceling] = useState(false);
  const [taskDetailError, setTaskDetailError] = useState<string | null>(null);
  const [taskDetailActionMessage, setTaskDetailActionMessage] = useState<string | null>(null);
  const [selectedTaskDetail, setSelectedTaskDetail] = useState<TaskDetailItem | null>(null);
  const [isTemplateDetailOpen, setIsTemplateDetailOpen] = useState(false);
  const [isTemplateDetailLoading, setIsTemplateDetailLoading] = useState(false);
  const [isTemplateDetailSaving, setIsTemplateDetailSaving] = useState(false);
  const [isTemplateDetailActionLoading, setIsTemplateDetailActionLoading] = useState(false);
  const [templateDetailError, setTemplateDetailError] = useState<string | null>(null);
  const [templateDetailSaveMessage, setTemplateDetailSaveMessage] = useState<string | null>(null);
  const [selectedTemplateDetail, setSelectedTemplateDetail] = useState<TaskTemplateDetailItem | null>(null);
  const [templateEditForm, setTemplateEditForm] = useState<TemplateEditFormState | null>(null);
  const [pendingConfirmAction, setPendingConfirmAction] = useState<PendingConfirmAction | null>(null);
  const [activeProject, setActiveProject] = useState<{ id: string | null; name: string | null; slug: string | null; objective?: string }>({ id: null, name: null, slug: null });
  const [clockNow, setClockNow] = useState(() => Date.now());
  const [pulseTrigger, setPulseTrigger] = useState(0);
  const lastPulse = useRef(0);
  const refreshInFlightRef = useRef<Promise<void> | null>(null);

  const triggerPulse = useCallback(() => {
    const now = Date.now();
    if (now - lastPulse.current > 2000) {
      setPulseTrigger((current) => current + 1);
      lastPulse.current = now;
    }
  }, []);

  const refreshDashboardSnapshot = useCallback(async () => {
    if (refreshInFlightRef.current) return refreshInFlightRef.current;

    const run = (async () => {
      try {
        const [m, business, objective, orchestrator, objectiveList, improvementStatusData, runTasks, upcoming, proj, activeProj] = await Promise.all([
          fetch(`${dsUrl}/api/metrics`).then((r) => r.json()),
          fetch(`${dsUrl}/api/metrics/business?hours=24&focus_limit=10`).then((r) => r.json()),
          fetch(`${dsUrl}/api/objectives/overview?hours=24&limit=10`).then((r) => r.json()),
          fetch(`${dsUrl}/api/metrics/orchestrator?hours=24`).then((r) => r.json()),
          fetch(`${dsUrl}/api/objectives?limit=30`).then((r) => r.json()),
          fetch(`${dsUrl}/api/improvement/status`).then(r => r.json()).catch(() => null),
          fetch(`${dsUrl}/api/tasks/recent?hours=24&limit=100`).then((r) => r.json()),
          fetch(`${dsUrl}/api/tasks/upcoming?awaiting_limit=120&scheduled_limit=120`).then((r) => r.json()),
          fetch(`${dsUrl}/api/projects`).then((r) => r.json()),
          fetch(`${dsUrl}/api/active-project`).then((r) => r.json()).catch(() => ({ id: null, name: null, slug: null })),
        ]);
        setMetrics(m && typeof m === 'object' && !m.detail ? m : null);
        setBusinessMetrics(business && typeof business === 'object' && !business.detail ? business : null);
        setObjectiveOverview(objective && typeof objective === 'object' && !objective.detail ? objective : null);
        setOrchestratorMetrics(orchestrator && typeof orchestrator === 'object' && !orchestrator.detail ? orchestrator : null);
        const normalizedObjectiveList = Array.isArray(objectiveList) ? (objectiveList as ObjectiveSummaryItem[]) : [];
        setObjectives(normalizedObjectiveList);
        setSelectedObjectiveId((current) => {
          if (current && normalizedObjectiveList.some((item) => item.id === current)) return current;
          const preferred = normalizedObjectiveList.find((item) => item.status === 'active') || normalizedObjectiveList[0];
          return preferred?.id || null;
        });
        setObjectiveRefreshTick((current) => current + 1);
        setImprovementStatus(improvementStatusData);
        setRecentTasks(Array.isArray(runTasks) ? runTasks : []);
        setUpcomingTasks({
          awaitingNow: Array.isArray(upcoming?.awaitingNow) ? upcoming.awaitingNow : [],
          scheduled: Array.isArray(upcoming?.scheduled) ? upcoming.scheduled : [],
        });
        setProjects(Array.isArray(proj) ? proj : []);
        if (activeProj && typeof activeProj === 'object') {
          setActiveProject(activeProj);
        }
      } catch (error) {
        console.error('Dashboard refresh failed', error);
      } finally {
        refreshInFlightRef.current = null;
      }
    })();

    refreshInFlightRef.current = run;
    return run;
  }, [dsUrl]);

  useEffect(() => {
    setProcessingAgentSnapshotByTaskId((current) => {
      const activeIds = new Set(recentTasks.filter((task) => task.status === 'running').map((task) => task.id));
      let changed = false;
      const next: Record<string, string> = {};

      for (const [taskId, label] of Object.entries(current)) {
        if (activeIds.has(taskId)) {
          next[taskId] = label;
        } else {
          changed = true;
        }
      }

      for (const task of recentTasks) {
        if (task.status !== 'running') continue;
        if (!(task.id in next)) {
          next[task.id] = task.agentName ? `@${task.agentName}` : 'No agent assigned';
          changed = true;
        }
      }

      return changed ? next : current;
    });
  }, [recentTasks]);

  // Fetch initial data.
  useEffect(() => {
    void refreshDashboardSnapshot();
  }, [refreshDashboardSnapshot]);

  // Local clock tick for relative time labels (no network).
  useEffect(() => {
    const timer = setInterval(() => {
      setClockNow(Date.now());
    }, UI_CLOCK_TICK_MS);
    return () => clearInterval(timer);
  }, []);

  // (agent list scroll unlock no-op: no agent list in bbclaw mode)

  // (selectedAgentId guard removed: no agent list in bbclaw mode)

  useEffect(() => {
    if (!isTaskDetailOpen && !isTemplateDetailOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsTaskDetailOpen(false);
        setIsTemplateDetailOpen(false);
        setPendingConfirmAction(null);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isTaskDetailOpen, isTemplateDetailOpen]);

  // SSE + resilience polling + stream health-check.
  useEffect(() => {
    let disposed = false;
    let sse: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let lastSseEventAt = Date.now();

    const clearReconnectTimer = () => {
      if (!reconnectTimer) return;
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    };

    const closeSse = () => {
      if (!sse) return;
      sse.close();
      sse = null;
    };

    const markSseEvent = () => {
      lastSseEventAt = Date.now();
    };

    const scheduleReconnect = (delayMs = 1500) => {
      if (disposed || reconnectTimer) return;
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        if (disposed) return;
        connectSse();
      }, delayMs);
    };

    const connectSse = () => {
      closeSse();
      clearReconnectTimer();
      markSseEvent();

      sse = new EventSource(`${dsUrl}/api/events`);

      sse.onopen = () => {
        markSseEvent();
      };

      sse.onmessage = (event) => {
        markSseEvent();
        try {
          const data = JSON.parse(event.data);

          if (data.type === 'request_finalized' || data.type === 'request_failed') {
            const requestId = typeof data?.payload?.requestId === 'string' ? data.payload.requestId : '';
            const message = typeof data?.payload?.message === 'string' ? data.payload.message : '';
            if (requestId && message) {
              setRequestCompletions((current) => [
                ...current,
                {
                  id: `${requestId}:${Date.now().toString(36)}:${Math.random().toString(36).slice(2, 7)}`,
                  requestId,
                  text: message,
                  status: data.type === 'request_failed' ? 'failed' : 'completed',
                },
              ]);
            }
            void refreshDashboardSnapshot();
          }

          if (data.type === 'task_status_changed' || data.type === 'task_run_started' || data.type === 'task_run_completed') {
            void refreshDashboardSnapshot();
            triggerPulse();
          }
        } catch (err) {
          console.error('SSE Error', err);
        }
      };

      sse.onerror = () => {
        if (disposed) return;
        if (!sse) return;
        if (sse.readyState === EventSource.CLOSED) {
          scheduleReconnect(1500);
        }
      };
    };

    connectSse();

    const resiliencePoll = setInterval(() => {
      if (document.visibilityState !== 'visible') return;
      void refreshDashboardSnapshot();
    }, RESILIENCE_POLL_MS);

    const streamHealthCheck = setInterval(() => {
      if (document.visibilityState !== 'visible') return;
      const silentMs = Date.now() - lastSseEventAt;
      if (silentMs <= SSE_STALE_MS) return;
      void refreshDashboardSnapshot();
      connectSse();
    }, SSE_HEALTHCHECK_MS);

    const onVisibilityChange = () => {
      if (document.visibilityState !== 'visible') return;
      void refreshDashboardSnapshot();
      const silentMs = Date.now() - lastSseEventAt;
      if (silentMs > SSE_STALE_MS) {
        connectSse();
      }
    };
    document.addEventListener('visibilitychange', onVisibilityChange);

    return () => {
      disposed = true;
      clearReconnectTimer();
      clearInterval(resiliencePoll);
      clearInterval(streamHealthCheck);
      document.removeEventListener('visibilitychange', onVisibilityChange);
      closeSse();
    };
  }, [dsUrl, refreshDashboardSnapshot, triggerPulse]);

  // Handle send message
  const handleSendMessage = async (msg: string, sessionId?: string): Promise<SendMessageResult> => {
    const res = await fetch(`${dsUrl}/api/prompt`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg, channel: 'web', sessionId }),
    }).then(r => r.json());

    const text = res?.humanMessage || res?.actionResponse?.message || res?.message || 'Instruction received and routed.';
    const requestId = typeof res?.requestId === 'string' ? res.requestId : undefined;
    const responseSessionId = typeof res?.sessionId === 'string' ? res.sessionId : undefined;
    const queued = res?.outcome === 'queued_async';
    setPulseTrigger(t => t + 1); // trigger lines
    return {
      text,
      requestId,
      queued,
      sessionId: responseSessionId,
    };
  };

  const selectedAgent = useMemo(
    () => null,
    []
  );

  const visibleRecentTasks = useMemo(() => {
    const agentFiltered = selectedAgentId
      ? recentTasks.filter((task) => task.agentId === selectedAgentId)
      : recentTasks;
    if (selectedAgentId) return agentFiltered;
    return agentFiltered.filter((task) => !task.parentTaskId);
  }, [recentTasks, selectedAgentId]);

  const visibleAwaitingNow = useMemo(() => {
    const agentFiltered = selectedAgentId
      ? upcomingTasks.awaitingNow.filter((task) => task.agentId === selectedAgentId)
      : upcomingTasks.awaitingNow;
    if (selectedAgentId) return agentFiltered;
    return agentFiltered.filter((task) => !task.parentTaskId);
  }, [upcomingTasks.awaitingNow, selectedAgentId]);

  const visibleScheduledTasks = useMemo(() => {
    const agentFiltered = selectedAgentId
      ? upcomingTasks.scheduled.filter((task) => task.agentId === selectedAgentId)
      : upcomingTasks.scheduled;
    if (selectedAgentId) return agentFiltered;
    return agentFiltered.filter((task) => task.kind === 'template' || !task.parentTaskId);
  }, [upcomingTasks.scheduled, selectedAgentId]);

  const visibleUpcomingCount = visibleAwaitingNow.length + visibleScheduledTasks.length;

  const visibleActiveAgentsCount = selectedAgentId
    ? selectedAgent?.isActive
      ? 1
      : 0
    : metrics?.activeAgents || 0;
  const visibleCompletedTasksCount = selectedAgentId
    ? visibleRecentTasks.filter((task) => task.status === 'completed').length
    : metrics?.tasks.completed || 0;
  const visiblePendingCount = visibleUpcomingCount;

  const handleAgentCardClick = useCallback((agentId: string) => {
    setSelectedAgentId((current) => (current === agentId ? null : agentId));
  }, []);

  const closeTaskDetail = useCallback(() => {
    setIsTaskDetailOpen(false);
    setPendingConfirmAction(null);
  }, []);

  const openTaskDetail = useCallback(
    async (task: TaskSummaryItem) => {
      setIsTemplateDetailOpen(false);
      setPendingConfirmAction(null);
      setIsTaskDetailOpen(true);
      setIsTaskDetailLoading(true);
      setIsTaskDetailCanceling(false);
      setTaskDetailError(null);
      setTaskDetailActionMessage(null);
      setSelectedTaskDetail({
        ...task,
        description: '',
        runHistory: [],
      });

      try {
        const response = await fetch(`${dsUrl}/api/tasks/${encodeURIComponent(task.id)}`);
        if (!response.ok) {
          throw new Error(`Could not load task detail (${response.status})`);
        }
        const detail = (await response.json()) as TaskDetailItem;
        setSelectedTaskDetail(detail);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setTaskDetailError(message);
      } finally {
        setIsTaskDetailLoading(false);
      }
    },
    [dsUrl]
  );

  const openTaskDetailById = useCallback(
    async (taskId: string) => {
      const normalizedId = taskId.trim();
      if (!normalizedId) return;

      const recent = recentTasks.find((task) => task.id === normalizedId);
      if (recent) {
        void openTaskDetail(recent);
        return;
      }

      const awaiting = upcomingTasks.awaitingNow.find((task) => task.id === normalizedId);
      if (awaiting) {
        void openTaskDetail(awaiting);
        return;
      }

      const scheduledTask = upcomingTasks.scheduled.find(
        (task) => task.kind === 'task' && (task.taskId || task.id) === normalizedId
      );
      if (scheduledTask && scheduledTask.kind === 'task') {
        void openTaskDetail(scheduledTask);
        return;
      }

      setIsTemplateDetailOpen(false);
      setPendingConfirmAction(null);
      setIsTaskDetailOpen(true);
      setIsTaskDetailLoading(true);
      setIsTaskDetailCanceling(false);
      setTaskDetailError(null);
      setTaskDetailActionMessage(null);
      setSelectedTaskDetail(null);

      try {
        const response = await fetch(`${dsUrl}/api/tasks/${encodeURIComponent(normalizedId)}`);
        if (!response.ok) {
          throw new Error(`Could not load task detail (${response.status})`);
        }
        const detail = (await response.json()) as TaskDetailItem;
        setSelectedTaskDetail(detail);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setTaskDetailError(message);
      } finally {
        setIsTaskDetailLoading(false);
      }
    },
    [dsUrl, openTaskDetail, recentTasks, upcomingTasks.awaitingNow, upcomingTasks.scheduled]
  );

  const loadObjectiveDetail = useCallback(
    async (objectiveId: string) => {
      const normalized = objectiveId.trim();
      if (!normalized) {
        setSelectedObjectiveDetail(null);
        return;
      }

      setIsObjectiveDetailLoading(true);
      try {
        const response = await fetch(`${dsUrl}/api/objectives/${encodeURIComponent(normalized)}`);
        if (!response.ok) {
          throw new Error(`Could not load objective detail (${response.status})`);
        }
        const detail = (await response.json()) as ObjectiveDetailItem;
        setSelectedObjectiveDetail(detail);
      } catch (error) {
        console.error('Objective detail load failed', error);
      } finally {
        setIsObjectiveDetailLoading(false);
      }
    },
    [dsUrl]
  );

  useEffect(() => {
    if (!selectedObjectiveId) {
      setSelectedObjectiveDetail(null);
      return;
    }
    void loadObjectiveDetail(selectedObjectiveId);
  }, [selectedObjectiveId, loadObjectiveDetail, objectiveRefreshTick]);

  const closeTemplateDetail = useCallback(() => {
    setIsTemplateDetailOpen(false);
    setPendingConfirmAction(null);
  }, []);

  const openTemplateDetail = useCallback(
    async (templateId: string) => {
      setIsTaskDetailOpen(false);
      setPendingConfirmAction(null);
      setIsTemplateDetailOpen(true);
      setIsTemplateDetailLoading(true);
      setIsTemplateDetailActionLoading(false);
      setTemplateDetailError(null);
      setTemplateDetailSaveMessage(null);
      setSelectedTemplateDetail(null);
      setTemplateEditForm(null);

      try {
        const response = await fetch(`${dsUrl}/api/task-templates/${encodeURIComponent(templateId)}`);
        if (!response.ok) {
          throw new Error(`Could not load template detail (${response.status})`);
        }
        const detail = (await response.json()) as TaskTemplateDetailItem;
        setSelectedTemplateDetail(detail);
        setTemplateEditForm(buildTemplateEditForm(detail));
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setTemplateDetailError(message);
      } finally {
        setIsTemplateDetailLoading(false);
      }
    },
    [dsUrl]
  );

  const toggleTemplateWeekday = useCallback((weekday: RecurrenceWeekday) => {
    setTemplateEditForm((current) => {
      if (!current) return current;
      const exists = current.byWeekday.includes(weekday);
      const nextWeekdays = exists
        ? current.byWeekday.filter((value) => value !== weekday)
        : [...current.byWeekday, weekday];
      return {
        ...current,
        byWeekday: sortWeekdays(nextWeekdays),
      };
    });
  }, []);

  const saveTemplateChanges = useCallback(async () => {
    if (!selectedTemplateDetail || !templateEditForm) return;
    setIsTemplateDetailSaving(true);
    setTemplateDetailError(null);
    setTemplateDetailSaveMessage(null);

    try {
      const recurrenceRule = buildTemplateRecurrenceRule(selectedTemplateDetail, templateEditForm);
      const response = await fetch(`${dsUrl}/api/task-templates/${encodeURIComponent(selectedTemplateDetail.id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: templateEditForm.title,
          description: templateEditForm.description,
          priority: templateEditForm.priority,
          timezone: templateEditForm.timezone,
          isActive: templateEditForm.isActive,
          recurrenceRule,
        }),
      });

      const payload = (await response.json().catch(() => ({}))) as {
        ok?: boolean;
        message?: string;
        template?: TaskTemplateDetailItem;
      };
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.message || `Could not update template (${response.status})`);
      }

      const nextDetail = payload.template || selectedTemplateDetail;
      setSelectedTemplateDetail(nextDetail);
      setTemplateEditForm(buildTemplateEditForm(nextDetail));
      setTemplateDetailSaveMessage(payload.message || 'Template updated.');
      void refreshDashboardSnapshot();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setTemplateDetailError(message);
    } finally {
      setIsTemplateDetailSaving(false);
    }
  }, [dsUrl, refreshDashboardSnapshot, selectedTemplateDetail, templateEditForm]);

  const cancelSelectedTask = useCallback(async () => {
    if (!selectedTaskDetail) return;
    setIsTaskDetailCanceling(true);
    setTaskDetailError(null);
    setTaskDetailActionMessage(null);

    try {
      const response = await fetch(`${dsUrl}/api/tasks/${encodeURIComponent(selectedTaskDetail.id)}/cancel`, {
        method: 'POST',
      });
      const payload = (await response.json().catch(() => ({}))) as {
        ok?: boolean;
        message?: string;
        task?: TaskDetailItem | null;
      };
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.message || `Could not cancel task (${response.status})`);
      }

      if (payload.task) {
        setSelectedTaskDetail(payload.task);
      } else {
        const detailResponse = await fetch(`${dsUrl}/api/tasks/${encodeURIComponent(selectedTaskDetail.id)}`);
        if (detailResponse.ok) {
          const detail = (await detailResponse.json()) as TaskDetailItem;
          setSelectedTaskDetail(detail);
        }
      }
      setTaskDetailActionMessage(payload.message || 'Task canceled.');
      void refreshDashboardSnapshot();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setTaskDetailError(message);
    } finally {
      setIsTaskDetailCanceling(false);
    }
  }, [dsUrl, refreshDashboardSnapshot, selectedTaskDetail]);

  const cancelNextTemplateOccurrence = useCallback(async () => {
    if (!selectedTemplateDetail) return;
    setIsTemplateDetailActionLoading(true);
    setTemplateDetailError(null);
    setTemplateDetailSaveMessage(null);

    try {
      const response = await fetch(
        `${dsUrl}/api/task-templates/${encodeURIComponent(selectedTemplateDetail.id)}/cancel-next`,
        { method: 'POST' }
      );
      const payload = (await response.json().catch(() => ({}))) as {
        ok?: boolean;
        message?: string;
        template?: TaskTemplateDetailItem | null;
      };
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.message || `Could not cancel next template occurrence (${response.status})`);
      }
      if (payload.template) {
        setSelectedTemplateDetail(payload.template);
        setTemplateEditForm(buildTemplateEditForm(payload.template));
      }
      setTemplateDetailSaveMessage(payload.message || 'Template occurrence canceled.');
      void refreshDashboardSnapshot();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setTemplateDetailError(message);
    } finally {
      setIsTemplateDetailActionLoading(false);
    }
  }, [dsUrl, refreshDashboardSnapshot, selectedTemplateDetail]);

  const deactivateSelectedTemplate = useCallback(async () => {
    if (!selectedTemplateDetail) return;
    setIsTemplateDetailActionLoading(true);
    setTemplateDetailError(null);
    setTemplateDetailSaveMessage(null);

    try {
      const response = await fetch(
        `${dsUrl}/api/task-templates/${encodeURIComponent(selectedTemplateDetail.id)}/deactivate`,
        { method: 'POST' }
      );
      const payload = (await response.json().catch(() => ({}))) as {
        ok?: boolean;
        message?: string;
        template?: TaskTemplateDetailItem | null;
      };
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.message || `Could not deactivate template (${response.status})`);
      }
      if (payload.template) {
        setSelectedTemplateDetail(payload.template);
        setTemplateEditForm(buildTemplateEditForm(payload.template));
      }
      setTemplateDetailSaveMessage(payload.message || 'Template deactivated.');
      void refreshDashboardSnapshot();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setTemplateDetailError(message);
    } finally {
      setIsTemplateDetailActionLoading(false);
    }
  }, [dsUrl, refreshDashboardSnapshot, selectedTemplateDetail]);

  const requestCancelSelectedTask = useCallback(() => {
    if (!selectedTaskDetail) return;
    setPendingConfirmAction({
      kind: 'cancel-task',
      taskId: selectedTaskDetail.id,
      taskTitle: selectedTaskDetail.title,
    });
  }, [selectedTaskDetail]);

  const requestCancelNextTemplateOccurrence = useCallback(() => {
    if (!selectedTemplateDetail) return;
    setPendingConfirmAction({
      kind: 'cancel-next-template-occurrence',
      templateId: selectedTemplateDetail.id,
      templateTitle: selectedTemplateDetail.title,
      nextRunAt: selectedTemplateDetail.nextRunAt,
    });
  }, [selectedTemplateDetail]);

  const requestDeactivateSelectedTemplate = useCallback(() => {
    if (!selectedTemplateDetail) return;
    setPendingConfirmAction({
      kind: 'deactivate-template-recurrence',
      templateId: selectedTemplateDetail.id,
      templateTitle: selectedTemplateDetail.title,
    });
  }, [selectedTemplateDetail]);

  const closeConfirmActionDialog = useCallback(() => {
    setPendingConfirmAction(null);
  }, []);

  const executeConfirmedAction = useCallback(async () => {
    if (!pendingConfirmAction) return;
    const action = pendingConfirmAction;
    setPendingConfirmAction(null);

    if (action.kind === 'cancel-task') {
      if (!selectedTaskDetail || selectedTaskDetail.id !== action.taskId) return;
      await cancelSelectedTask();
      return;
    }

    if (!selectedTemplateDetail || selectedTemplateDetail.id !== action.templateId) return;

    if (action.kind === 'cancel-next-template-occurrence') {
      await cancelNextTemplateOccurrence();
      return;
    }

    await deactivateSelectedTemplate();
  }, [
    cancelNextTemplateOccurrence,
    cancelSelectedTask,
    deactivateSelectedTemplate,
    pendingConfirmAction,
    selectedTaskDetail,
    selectedTemplateDetail,
  ]);

  const confirmActionDialog = useMemo(() => {
    if (!pendingConfirmAction) return null;
    if (pendingConfirmAction.kind === 'cancel-task') {
      return {
        title: 'Cancel this task?',
        message: `The task "${pendingConfirmAction.taskTitle}" will be marked as canceled and it will not run.`,
        confirmLabel: 'Cancel Task',
        variant: 'danger' as const,
      };
    }

    if (pendingConfirmAction.kind === 'cancel-next-template-occurrence') {
      return {
        title: 'Cancel only the next occurrence?',
        message: `This skips the next run (${formatAbsoluteDateTime(pendingConfirmAction.nextRunAt)}) of "${pendingConfirmAction.templateTitle}". The recurring template stays active.`,
        confirmLabel: 'Cancel Next Occurrence',
        variant: 'warning' as const,
      };
    }

    return {
      title: 'Deactivate recurring template?',
      message: `This stops all future runs of "${pendingConfirmAction.templateTitle}". Existing materialized tasks are not removed.`,
      confirmLabel: 'Deactivate Recurrence',
      variant: 'danger' as const,
    };
  }, [pendingConfirmAction]);

  const isConfirmActionBusy = useMemo(() => {
    if (!pendingConfirmAction) return false;
    if (pendingConfirmAction.kind === 'cancel-task') {
      return isTaskDetailCanceling || isTaskDetailLoading;
    }
    return isTemplateDetailActionLoading || isTemplateDetailSaving || isTemplateDetailLoading;
  }, [
    isTaskDetailCanceling,
    isTaskDetailLoading,
    isTemplateDetailActionLoading,
    isTemplateDetailLoading,
    isTemplateDetailSaving,
    pendingConfirmAction,
  ]);

  const detailOutputText = (selectedTaskDetail?.outputResult || selectedTaskDetail?.latestRun?.outputResult || '').trim();
  const detailErrorText = (selectedTaskDetail?.latestRun?.errorMessage || '').trim();
  const canCancelSelectedTask =
    selectedTaskDetail?.status === 'pending' || selectedTaskDetail?.status === 'blocked';

  return (
    <div className="app-container">
      {/* Background SVG Flow Lines */}
      <ConnectionLines eventTrigger={pulseTrigger} />

      <div className="dashboard-mode-bar glass-panel">
        <div className="dashboard-mode-title">
          <h2>Executive Control Center</h2>
          <p>{dashboardMode === 'business' ? 'Business-first overview with drill-down' : 'Live task operations and agent workload'}</p>
        </div>
        <div className="dashboard-mode-actions">
          <button
            type="button"
            className={`dashboard-mode-btn${dashboardMode === 'live' ? ' active' : ''}`}
            onClick={() => setDashboardMode('live')}
          >
            Live Ops
          </button>
          <button
            type="button"
            className={`dashboard-mode-btn${dashboardMode === 'business' ? ' active' : ''}`}
            onClick={() => setDashboardMode('business')}
          >
            Business Overview
          </button>
        </div>
      </div>

      {dashboardMode === 'live' ? (
        <>

      {/* 1. Left Orb / Vitals */}
      <motion.div
        className="vitass-col"
        style={{ flex: '0 0 350px', marginTop: 'var(--live-dashboard-offset)', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', zIndex: 10 }}
        initial={{ opacity: 0, x: -50 }} animate={{ opacity: 1, x: 0 }}
      >
        <div style={{
          background: 'rgba(255,255,255,0.03)', padding: '0.6rem 2.25rem', borderRadius: '30px',
          border: '1px solid var(--border-card)', marginBottom: '5rem', fontSize: '0.85rem',
          fontWeight: 600, letterSpacing: '0.15em', textTransform: 'uppercase',
          boxShadow: '0 8px 32px rgba(0,0,0,0.4)', backdropFilter: 'blur(10px)'
        }}>
          System Online
        </div>

        <Orb pendingCount={visiblePendingCount} activeAgents={visibleActiveAgentsCount} />

        <div style={{ marginTop: '5rem', display: 'flex', gap: '2rem' }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
            <span style={{ fontSize: '2.5rem', fontFamily: 'Outfit, sans-serif', fontWeight: 700, lineHeight: 1, color: 'var(--text-primary)' }}>
              <AnimatedNumber value={metrics?.tasks?.running ?? 0} />
            </span>
            <span style={{ fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-tertiary)', marginTop: '8px' }}>Running Tasks</span>
          </div>
          <div style={{ width: '1px', background: 'var(--border-card)' }} />
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
            <span style={{ fontSize: '2.5rem', fontFamily: 'Outfit, sans-serif', fontWeight: 700, lineHeight: 1, color: 'var(--text-primary)' }}>
              <AnimatedNumber value={visibleCompletedTasksCount} />
            </span>
            <span style={{ fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-tertiary)', marginTop: '8px' }}>Completed Tasks</span>
            <span style={{ fontSize: '0.65rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--accent-primary)', marginTop: '4px', opacity: 0.8 }}>Last 24hs</span>
          </div>
        </div>
        <p style={{ marginTop: '1.2rem', fontSize: '0.72rem', letterSpacing: '0.04em', color: 'var(--text-tertiary)', textTransform: 'uppercase' }}>
          {selectedAgent ? `Filter: @${selectedAgent.name}` : 'Filter: all agents'}
        </p>
      </motion.div>


      {/* Main Grid: 3 Columns */}
      <div style={{ flex: 1, marginTop: 'var(--live-dashboard-offset)', display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '2rem', overflow: 'hidden', zIndex: 10 }}>

        {/* Col 1: Agents */}
        <div className="glass-panel column-panel column-agents" style={{ display: 'flex', flexDirection: 'column', padding: '1.5rem', overflow: 'hidden' }}>
          {/* Loop Status — bbclaw specific */}
          <div className="glass-panel" style={{padding: "1rem", display: "flex", flexDirection: "column", gap: "0.75rem", overflowY: "auto"}}>
            <div style={{fontSize: "0.75rem", fontWeight: 600, color: "var(--text-secondary, #94a3b8)", textTransform: "uppercase", letterSpacing: "0.05em"}}>
              Loop Status
            </div>

            {/* Autonomous Loop — first, runs frequently */}
            <div className="glass-card" style={{padding: "0.75rem"}}>
              <div style={{display: "flex", justifyContent: "space-between", alignItems: "center"}}>
                <span style={{fontSize: "0.8rem", fontWeight: 600}}>🎯 Autonomous Loop</span>
                <span className={`status-chip ${improvementStatus?.autonomousLoop.isRunning ? "status-running" : "status-idle"}`}>
                  {improvementStatus?.autonomousLoop.isRunning ? "Active" : "Idle"}
                </span>
              </div>
              {(() => {
                const lastTick = improvementStatus?.autonomousLoop.lastTickAt;
                if (lastTick) {
                  const elapsedMs = clockNow - new Date(lastTick).getTime();
                  const mins = Math.floor(elapsedMs / 60_000);
                  const label = mins < 1 ? "just now" : `${mins}m ago`;
                  return (
                    <div style={{fontSize: "0.7rem", color: "var(--text-secondary, #94a3b8)", marginTop: "0.3rem"}}>
                      Last cycle: {label}
                    </div>
                  );
                }
                return (
                  <div style={{fontSize: "0.7rem", color: "var(--text-secondary, #94a3b8)", marginTop: "0.3rem"}}>
                    Last cycle: waiting for first tick
                  </div>
                );
              })()}
              <div style={{fontSize: "0.7rem", color: "var(--text-secondary, #94a3b8)", marginTop: "0.2rem"}}>
                {improvementStatus?.autonomousLoop.projectsWithObjective ?? 0} projects with objective · {improvementStatus?.autonomousLoop.activeScheduledItems ?? 0} scheduled items
              </div>
            </div>

            {/* Providers */}
            {improvementStatus?.providers && improvementStatus.providers.length > 0 && (
              <div className="glass-card" style={{padding: "0.75rem"}}>
                <div style={{fontSize: "0.8rem", fontWeight: 600, marginBottom: "0.4rem"}}>⚡ Providers</div>
                {improvementStatus.providers.map(p => (
                  <div key={p.name} style={{display: "flex", justifyContent: "space-between", fontSize: "0.75rem", padding: "0.2rem 0"}}>
                    <span>{p.name}</span>
                    <span style={{color: p.state === "CLOSED" ? "#22c55e" : p.state === "OPEN" ? "#ef4444" : "#f59e0b"}}>
                      {p.state === "CLOSED" ? "✓" : p.state === "OPEN" ? "✗" : "~"} {p.state}
                    </span>
                  </div>
                ))}
              </div>
            )}

            {/* Improvement Loop — last, runs every 6h */}
            <div className="glass-card" style={{padding: "0.75rem"}}>
              <div style={{display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem"}}>
                <span style={{fontSize: "0.8rem", fontWeight: 600}}>🔄 Improvement Loop</span>
                <span className={`status-chip ${improvementStatus?.improvementLoop.isRunning ? "status-running" : "status-idle"}`}>
                  {improvementStatus?.improvementLoop.isRunning ? "Running" : "Idle"}
                </span>
              </div>
              {/* Next cycle countdown */}
              {(() => {
                const nextRun = improvementStatus?.improvementLoop.nextRunAt;
                const intervalMin = improvementStatus?.improvementLoop.intervalMinutes ?? 360;
                if (nextRun) {
                  const nextMs = new Date(nextRun).getTime();
                  const remainMs = nextMs - clockNow;
                  if (remainMs > 0) {
                    const h = Math.floor(remainMs / 3_600_000);
                    const m = Math.floor((remainMs % 3_600_000) / 60_000);
                    return (
                      <div style={{fontSize: "0.75rem", color: "var(--text-secondary, #94a3b8)"}}>
                        Next cycle: <span style={{color: "var(--text-primary)", fontWeight: 600}}>{h}h {m}m</span>
                      </div>
                    );
                  }
                  return (
                    <div style={{fontSize: "0.75rem", color: "#f59e0b"}}>
                      Next cycle: waiting for idle window
                    </div>
                  );
                }
                return (
                  <div style={{fontSize: "0.75rem", color: "var(--text-secondary, #94a3b8)"}}>
                    Next cycle: first run in ~{Math.round(intervalMin / 60)}h
                  </div>
                );
              })()}
              {improvementStatus?.improvementLoop.lastScoreDelta != null && (
                <div style={{fontSize: "0.75rem", color: improvementStatus.improvementLoop.lastScoreDelta >= 0 ? "#22c55e" : "#ef4444", marginTop: "0.25rem"}}>
                  Last Δ: {improvementStatus.improvementLoop.lastScoreDelta >= 0 ? "+" : ""}{improvementStatus.improvementLoop.lastScoreDelta.toFixed(4)}
                </div>
              )}
              {improvementStatus?.improvementLoop.consecutiveNoImprovement > 0 && (
                <div style={{fontSize: "0.75rem", color: "#f59e0b", marginTop: "0.25rem"}}>
                  Stale: {improvementStatus.improvementLoop.consecutiveNoImprovement} cycles without improvement
                </div>
              )}
              {(() => {
                const tokens = improvementStatus?.improvementLoop.lastCycleTokens ?? 0;
                const budget = improvementStatus?.improvementLoop.tokenBudget ?? 80000;
                const pct = budget > 0 ? Math.min(100, (tokens / budget) * 100) : 0;
                return (
                  <div style={{marginTop: "0.3rem"}}>
                    <div style={{fontSize: "0.7rem", color: "var(--text-secondary, #94a3b8)"}}>
                      Last cycle tokens: {tokens > 0 ? `${(tokens / 1000).toFixed(1)}k / ${(budget / 1000).toFixed(0)}k budget` : `No data yet (budget: ${(budget / 1000).toFixed(0)}k)`}
                    </div>
                    {tokens > 0 && (
                      <div style={{background: "rgba(255,255,255,0.08)", borderRadius: "4px", height: "6px", overflow: "hidden", marginTop: "0.25rem"}}>
                        <div style={{
                          background: pct > 80 ? "#ef4444" : "#6366f1",
                          width: `${pct}%`,
                          height: "100%", borderRadius: "4px", transition: "width 0.5s ease"
                        }} />
                      </div>
                    )}
                  </div>
                );
              })()}
              {(() => {
                const score = improvementStatus?.behavioralSuite?.lastScore;
                const passed = improvementStatus?.behavioralSuite?.casesPassed ?? 0;
                const total = improvementStatus?.behavioralSuite?.casesTotal ?? 0;
                const hasData = total > 0;
                const color = hasData ? (score! >= 0.8 ? "#22c55e" : score! >= 0.5 ? "#f59e0b" : "#ef4444") : "var(--text-secondary, #94a3b8)";
                return (
                  <div style={{fontSize: "0.7rem", color: "var(--text-secondary, #94a3b8)", marginTop: "0.3rem"}}>
                    Behavioral score: {hasData
                      ? <span style={{color, fontWeight: 600}}>{(score! * 100).toFixed(1)}% ({passed}/{total} cases)</span>
                      : "No data yet"}
                  </div>
                );
              })()}
            </div>
          </div>
        </div>

        {/* Col 2: Active Tasks */}
        <div className="glass-panel column-panel column-recent" style={{ display: 'flex', flexDirection: 'column', padding: '1.5rem', overflow: 'hidden' }}>
          <h2 className="column-header">
            <span>{selectedAgent ? 'Recent Tasks' : 'Parent Tasks'}</span>
            <span className="column-subtitle"><AnimatedNumber value={visibleRecentTasks.length} /> Last 24hs</span>
          </h2>

          <div className="task-list">
            <AnimatePresence initial={false} mode="popLayout">
              {visibleRecentTasks.map((t) => (
                <motion.div
                  key={t.id}
                  layout="position"
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -6 }}
                  transition={taskItemMotionTransition}
                >
                  <TaskCard
                    task={t}
                    lane="recent"
                    processingAgentLabel={processingAgentSnapshotByTaskId[t.id]}
                    nowMs={clockNow}
                    onClick={() => void openTaskDetail(t)}
                  />
                </motion.div>
              ))}
            </AnimatePresence>
            <p className={`task-empty-message ${visibleRecentTasks.length === 0 ? 'visible' : 'hidden'}`}>
              {selectedAgent
                ? `No recent tasks for @${selectedAgent.name} in the last 24hs.`
                : 'No parent tasks in the last 24hs.'}
            </p>
          </div>
        </div>

        {/* Col 3: Upcoming Tasks */}
        <div className="glass-panel column-panel column-awaiting" style={{ display: 'flex', flexDirection: 'column', padding: '1.5rem', overflow: 'hidden' }}>
          <h2 className="column-header">
            <span>{selectedAgent ? 'Upcoming Tasks' : 'Upcoming Parent Tasks'}</span>
            <span className="column-subtitle"><AnimatedNumber value={visibleUpcomingCount} /> Total</span>
          </h2>

          <div className="task-list upcoming-sections">
            <section className="upcoming-section">
              <div className="upcoming-section-header">
                <span>Awaiting now</span>
                <span>{visibleAwaitingNow.length}</span>
              </div>
              <AnimatePresence initial={false} mode="popLayout">
                {visibleAwaitingNow.map((task) => (
                  <motion.div
                    key={`awaiting:${task.id}`}
                    layout="position"
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -6 }}
                    transition={taskItemMotionTransition}
                  >
                    <TaskCard task={task} lane="awaiting" nowMs={clockNow} onClick={() => void openTaskDetail(task)} />
                  </motion.div>
                ))}
              </AnimatePresence>
              <p className={`task-empty-message section-empty ${visibleAwaitingNow.length === 0 ? 'visible' : 'hidden'}`}>
                {selectedAgent ? `No awaiting tasks for @${selectedAgent.name}.` : 'No awaiting tasks right now.'}
              </p>
            </section>

            <section className="upcoming-section">
              <div className="upcoming-section-header">
                <span>Scheduled</span>
                <span>{visibleScheduledTasks.length}</span>
              </div>
              <AnimatePresence initial={false} mode="popLayout">
                {visibleScheduledTasks.map((task) => (
                  <motion.div
                    key={`scheduled:${task.kind}:${task.id}`}
                    layout="position"
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -6 }}
                    transition={taskItemMotionTransition}
                  >
                    <TaskCard
                      task={task}
                      lane="scheduled"
                      nowMs={clockNow}
                      onClick={
                        task.kind === 'task'
                          ? () => void openTaskDetail(task)
                          : () => void openTemplateDetail(task.templateId || task.id)
                      }
                    />
                  </motion.div>
                ))}
              </AnimatePresence>
              <p className={`task-empty-message section-empty ${visibleScheduledTasks.length === 0 ? 'visible' : 'hidden'}`}>
                {selectedAgent ? `No scheduled tasks for @${selectedAgent.name}.` : 'No scheduled tasks.'}
              </p>
            </section>
          </div>
        </div>
      </div>
        </>
      ) : (
        <BusinessOverviewPanel
          businessMetrics={businessMetrics}
          objectiveMetrics={objectiveOverview}
          orchestratorMetrics={orchestratorMetrics}
          objectives={objectives}
          selectedObjectiveId={selectedObjectiveId}
          objectiveDetail={selectedObjectiveDetail}
          isObjectiveLoading={isObjectiveDetailLoading}
          onSelectObjective={setSelectedObjectiveId}
          onOpenTask={(taskId) => void openTaskDetailById(taskId)}
        />
      )}

      <AnimatePresence>
        {isTaskDetailOpen ? (
          <motion.div
            className="task-detail-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={closeTaskDetail}
          >
            <motion.div
              className="task-detail-modal glass-panel"
              initial={{ opacity: 0, y: 14, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 10, scale: 0.98 }}
              transition={{ duration: 0.2, ease: 'easeOut' }}
              onClick={(event) => event.stopPropagation()}
            >
              <div className="task-detail-header">
                <div>
                  <h3 className="task-detail-title">{selectedTaskDetail?.title || 'Task detail'}</h3>
                  <p className="task-detail-subtitle">ID: {selectedTaskDetail?.id || 'N/A'}</p>
                </div>
                <button type="button" className="task-detail-close" onClick={closeTaskDetail}>
                  Close
                </button>
              </div>

              {taskDetailError ? <p className="task-detail-error">Could not refresh full detail: {taskDetailError}</p> : null}
              {taskDetailActionMessage ? <p className="task-detail-success">{taskDetailActionMessage}</p> : null}
              {isTaskDetailLoading ? <p className="task-detail-loading">Loading task detail...</p> : null}

              {selectedTaskDetail ? (
                <div className="task-detail-scroll">
                  <div className="task-detail-chips">
                    <span className={`task-state-chip processing ${taskStatusClass(selectedTaskDetail.status)}`}>
                      {formatTaskStatusLabel(selectedTaskDetail.status)}
                    </span>
                    <span className="chip neutral">Project: {selectedTaskDetail.projectName || 'General'}</span>
                    <span className="chip neutral">
                      {selectedTaskDetail.agentName ? `@${selectedTaskDetail.agentName}` : 'No agent assigned'}
                    </span>
                    <span className="chip neutral">Priority {selectedTaskDetail.priority}</span>
                  </div>

                  {canCancelSelectedTask ? (
                    <div className="task-detail-actions">
                      <button
                        type="button"
                        className="task-cancel-btn"
                        onClick={requestCancelSelectedTask}
                        disabled={isTaskDetailCanceling || isTaskDetailLoading}
                      >
                        {isTaskDetailCanceling ? 'Canceling...' : 'Cancel Task'}
                      </button>
                    </div>
                  ) : null}

                  <div className="task-detail-meta-grid">
                    <div>
                      <span>Created</span>
                      <strong>{formatAbsoluteDateTime(selectedTaskDetail.createdAt)}</strong>
                    </div>
                    <div>
                      <span>Updated</span>
                      <strong>{formatAbsoluteDateTime(selectedTaskDetail.updatedAt)}</strong>
                    </div>
                    <div>
                      <span>Due</span>
                      <strong>{formatAbsoluteDateTime(selectedTaskDetail.dueAt)}</strong>
                    </div>
                    <div>
                      <span>Completed</span>
                      <strong>{formatAbsoluteDateTime(selectedTaskDetail.completedAt)}</strong>
                    </div>
                  </div>

                  <section className="task-detail-section">
                    <h4>Description</h4>
                    <pre className="task-detail-pre">
                      {selectedTaskDetail.description?.trim() || 'No description available for this task.'}
                    </pre>
                  </section>

                  <section className="task-detail-section">
                    <h4>Output</h4>
                    <pre className="task-detail-pre">
                      {detailOutputText || 'This task has no output captured yet.'}
                    </pre>
                  </section>

                  {detailErrorText ? (
                    <section className="task-detail-section">
                      <h4>Latest Error</h4>
                      <pre className="task-detail-pre task-detail-pre-error">{detailErrorText}</pre>
                    </section>
                  ) : null}

                  {selectedTaskDetail.latestRun ? (
                    <section className="task-detail-section">
                      <h4>Latest Run</h4>
                      <div className="task-detail-meta-grid">
                        <div>
                          <span>Status</span>
                          <strong>{formatTaskStatusLabel(selectedTaskDetail.latestRun.status)}</strong>
                        </div>
                        <div>
                          <span>Duration</span>
                          <strong>{formatDurationMs(selectedTaskDetail.latestRun.durationMs)}</strong>
                        </div>
                        <div>
                          <span>Model</span>
                          <strong>{selectedTaskDetail.latestRun.model || 'N/A'}</strong>
                        </div>
                        <div>
                          <span>Tokens</span>
                          <strong>{selectedTaskDetail.latestRun.totalTokens}</strong>
                        </div>
                      </div>
                    </section>
                  ) : null}

                  <section className="task-detail-section">
                    <h4>Run History</h4>
                    {selectedTaskDetail.runHistory.length === 0 ? (
                      <p className="task-detail-empty">No run history available.</p>
                    ) : (
                      <div className="task-run-history">
                        {selectedTaskDetail.runHistory.map((run) => (
                          <div className="task-run-row" key={run.id}>
                            <div className="task-run-row-main">
                              <span className={`task-state-chip processing ${taskStatusClass(run.status)}`}>
                                {formatTaskStatusLabel(run.status)}
                              </span>
                              <span>Attempt {run.attemptNumber}</span>
                              <span>{run.agentName ? `@${run.agentName}` : 'Unknown agent'}</span>
                            </div>
                            <div className="task-run-row-meta">
                              <span>{formatAbsoluteDateTime(run.createdAt)}</span>
                              <span>{formatDurationMs(run.durationMs)}</span>
                              <span>{run.totalTokens} tokens</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </section>
                </div>
              ) : null}
            </motion.div>
          </motion.div>
        ) : null}
      </AnimatePresence>

      <AnimatePresence>
        {isTemplateDetailOpen ? (
          <motion.div
            className="task-detail-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={closeTemplateDetail}
          >
            <motion.div
              className="task-detail-modal glass-panel"
              initial={{ opacity: 0, y: 14, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 10, scale: 0.98 }}
              transition={{ duration: 0.2, ease: 'easeOut' }}
              onClick={(event) => event.stopPropagation()}
            >
              <div className="task-detail-header">
                <div>
                  <h3 className="task-detail-title">{selectedTemplateDetail?.title || 'Task template detail'}</h3>
                  <p className="task-detail-subtitle">TEMPLATE ID: {selectedTemplateDetail?.id || 'N/A'}</p>
                </div>
                <button type="button" className="task-detail-close" onClick={closeTemplateDetail}>
                  Close
                </button>
              </div>

              {templateDetailError ? <p className="task-detail-error">Could not load/update template: {templateDetailError}</p> : null}
              {templateDetailSaveMessage ? <p className="task-detail-success">{templateDetailSaveMessage}</p> : null}
              {isTemplateDetailLoading ? <p className="task-detail-loading">Loading template detail...</p> : null}

              {selectedTemplateDetail && templateEditForm ? (
                <div className="task-detail-scroll">
                  <div className="task-detail-chips">
                    <span className={`task-state-chip awaiting ${selectedTemplateDetail.isActive ? 'status-scheduled' : 'status-canceled'}`}>
                      {selectedTemplateDetail.isActive ? 'Active template' : 'Inactive template'}
                    </span>
                    <span className="chip neutral">Project: {selectedTemplateDetail.projectName || 'General'}</span>
                    <span className="chip neutral">
                      {selectedTemplateDetail.assignedAgentName
                        ? `@${selectedTemplateDetail.assignedAgentName}`
                        : selectedTemplateDetail.assignedUserName
                          ? selectedTemplateDetail.assignedUserName
                          : 'Unassigned'}
                    </span>
                  </div>

                  <div className="task-detail-meta-grid">
                    <div>
                      <span>Created</span>
                      <strong>{formatAbsoluteDateTime(selectedTemplateDetail.createdAt)}</strong>
                    </div>
                    <div>
                      <span>Updated</span>
                      <strong>{formatAbsoluteDateTime(selectedTemplateDetail.updatedAt)}</strong>
                    </div>
                    <div>
                      <span>Next Run</span>
                      <strong>{formatAbsoluteDateTime(selectedTemplateDetail.nextRunAt)}</strong>
                    </div>
                    <div>
                      <span>Last Run</span>
                      <strong>{formatAbsoluteDateTime(selectedTemplateDetail.lastRunAt)}</strong>
                    </div>
                  </div>

                  <section className="task-detail-section">
                    <h4>Template Config</h4>
                    <div className="template-form-grid">
                      <label className="template-form-field">
                        <span>Title</span>
                        <input
                          value={templateEditForm.title}
                          onChange={(event) =>
                            setTemplateEditForm((current) =>
                              current ? { ...current, title: event.target.value } : current
                            )
                          }
                        />
                      </label>

                      <label className="template-form-field">
                        <span>Priority</span>
                        <select
                          value={templateEditForm.priority}
                          onChange={(event) =>
                            setTemplateEditForm((current) =>
                              current ? { ...current, priority: Math.max(1, Math.min(5, Number(event.target.value) || 3)) } : current
                            )
                          }
                        >
                          <option value={1}>1 - Highest</option>
                          <option value={2}>2</option>
                          <option value={3}>3</option>
                          <option value={4}>4</option>
                          <option value={5}>5 - Lowest</option>
                        </select>
                      </label>

                      <label className="template-form-field">
                        <span>Timezone</span>
                        <input
                          value={templateEditForm.timezone}
                          onChange={(event) =>
                            setTemplateEditForm((current) =>
                              current ? { ...current, timezone: event.target.value } : current
                            )
                          }
                          placeholder="UTC"
                        />
                      </label>

                      <label className="template-form-field">
                        <span>Status</span>
                        <select
                          value={templateEditForm.isActive ? 'active' : 'inactive'}
                          onChange={(event) =>
                            setTemplateEditForm((current) =>
                              current ? { ...current, isActive: event.target.value === 'active' } : current
                            )
                          }
                        >
                          <option value="active">Active</option>
                          <option value="inactive">Inactive</option>
                        </select>
                      </label>

                      <label className="template-form-field template-form-field-wide">
                        <span>Prompt / Description</span>
                        <textarea
                          value={templateEditForm.description}
                          onChange={(event) =>
                            setTemplateEditForm((current) =>
                              current ? { ...current, description: event.target.value } : current
                            )
                          }
                          rows={5}
                        />
                      </label>
                    </div>
                  </section>

                  <section className="task-detail-section">
                    <h4>Schedule</h4>
                    <div className="template-form-grid">
                      <label className="template-form-field">
                        <span>Frequency</span>
                        <select
                          value={templateEditForm.freq}
                          onChange={(event) =>
                            setTemplateEditForm((current) =>
                              current ? { ...current, freq: event.target.value as RecurrenceFreq } : current
                            )
                          }
                        >
                          <option value="daily">Daily</option>
                          <option value="weekly">Weekly</option>
                          <option value="monthly">Monthly</option>
                          <option value="yearly">Yearly</option>
                        </select>
                      </label>

                      <label className="template-form-field">
                        <span>Interval</span>
                        <input
                          type="number"
                          min={1}
                          max={3650}
                          value={templateEditForm.interval}
                          onChange={(event) =>
                            setTemplateEditForm((current) =>
                              current
                                ? { ...current, interval: Math.max(1, Math.min(3650, Number(event.target.value) || 1)) }
                                : current
                            )
                          }
                        />
                      </label>

                      <label className="template-form-field">
                        <span>Run Time</span>
                        <input
                          type="time"
                          value={templateEditForm.anchorTime}
                          onChange={(event) =>
                            setTemplateEditForm((current) =>
                              current ? { ...current, anchorTime: event.target.value } : current
                            )
                          }
                        />
                      </label>

                      {templateEditForm.freq === 'weekly' ? (
                        <div className="template-form-field template-form-field-wide">
                          <span>Weekdays</span>
                          <div className="weekday-toggle-row">
                            {WEEKDAY_OPTIONS.map((weekday) => (
                              <button
                                key={weekday.value}
                                type="button"
                                className={`weekday-toggle${templateEditForm.byWeekday.includes(weekday.value) ? ' selected' : ''}`}
                                onClick={() => toggleTemplateWeekday(weekday.value)}
                              >
                                {weekday.label}
                              </button>
                            ))}
                          </div>
                        </div>
                      ) : null}
                    </div>
                  </section>

                  <section className="task-detail-section">
                    <h4>Materialized Tasks</h4>
                    <div className="task-detail-meta-grid">
                      <div>
                        <span>Total</span>
                        <strong>{selectedTemplateDetail.totalMaterializedTasks}</strong>
                      </div>
                      <div>
                        <span>Pending</span>
                        <strong>{selectedTemplateDetail.pendingMaterializedTasks}</strong>
                      </div>
                      <div>
                        <span>Running</span>
                        <strong>{selectedTemplateDetail.runningMaterializedTasks}</strong>
                      </div>
                      <div>
                        <span>Completed</span>
                        <strong>{selectedTemplateDetail.completedMaterializedTasks}</strong>
                      </div>
                    </div>
                  </section>

                  <div className="template-detail-actions">
                    <div className="template-action-group">
                      <button
                        type="button"
                        className="template-skip-btn"
                        onClick={requestCancelNextTemplateOccurrence}
                        disabled={!selectedTemplateDetail.isActive || isTemplateDetailSaving || isTemplateDetailActionLoading}
                      >
                        {isTemplateDetailActionLoading ? 'Working...' : 'Cancel Next Occurrence'}
                      </button>
                      <button
                        type="button"
                        className="template-danger-btn"
                        onClick={requestDeactivateSelectedTemplate}
                        disabled={!selectedTemplateDetail.isActive || isTemplateDetailSaving || isTemplateDetailActionLoading}
                      >
                        {isTemplateDetailActionLoading ? 'Working...' : 'Deactivate Recurrence'}
                      </button>
                    </div>
                    <div className="template-action-group">
                      <button
                        type="button"
                        className="template-save-btn"
                        onClick={() => void saveTemplateChanges()}
                        disabled={isTemplateDetailSaving || isTemplateDetailActionLoading}
                      >
                        {isTemplateDetailSaving ? 'Saving...' : 'Save Template'}
                      </button>
                    </div>
                  </div>
                </div>
              ) : null}
            </motion.div>
          </motion.div>
        ) : null}
      </AnimatePresence>

      <AnimatePresence>
        {confirmActionDialog ? (
          <motion.div
            className="action-confirm-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={closeConfirmActionDialog}
          >
            <motion.div
              className="action-confirm-modal glass-panel"
              initial={{ opacity: 0, y: 10, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 8, scale: 0.98 }}
              transition={{ duration: 0.18, ease: 'easeOut' }}
              onClick={(event) => event.stopPropagation()}
            >
              <h4 className="action-confirm-title">{confirmActionDialog.title}</h4>
              <p className="action-confirm-message">{confirmActionDialog.message}</p>
              <div className="action-confirm-actions">
                <button type="button" className="action-confirm-cancel-btn" onClick={closeConfirmActionDialog}>
                  Keep As Is
                </button>
                <button
                  type="button"
                  className={`action-confirm-accept-btn ${
                    confirmActionDialog.variant === 'warning' ? 'variant-warning' : 'variant-danger'
                  }`}
                  onClick={() => void executeConfirmedAction()}
                  disabled={isConfirmActionBusy}
                >
                  {isConfirmActionBusy ? 'Working...' : confirmActionDialog.confirmLabel}
                </button>
              </div>
            </motion.div>
          </motion.div>
        ) : null}
      </AnimatePresence>

      <ChatInput
        onSendMessage={handleSendMessage}
        agents={[]}
        projects={projects}
        activeProjectName={activeProject.name}
        requestCompletions={requestCompletions}
        apiBaseUrl={dsUrl}
      />
    </div>
  );
}

export default App;
