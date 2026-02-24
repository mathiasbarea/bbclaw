export interface TaskSummaryItem {
    id: string;
    title: string;
    status: string;
    priority: number;
    createdBy?: string;
    projectId: string;
    projectName?: string;
    parentTaskId?: string;
    agentId?: string;
    agentName?: string;
    dueAt?: number;
    timezone?: string;
    createdAt: number;
    updatedAt: number;
}

export interface UpcomingTaskItem extends TaskSummaryItem {
    kind: 'task' | 'template';
    taskId?: string;
    templateId?: string;
}

export interface UpcomingTasksOverview {
    awaitingNow: UpcomingTaskItem[];
    scheduled: UpcomingTaskItem[];
}

export type RecurrenceFreq = 'daily' | 'weekly' | 'monthly' | 'yearly';
export type RecurrenceWeekday =
    | 'monday'
    | 'tuesday'
    | 'wednesday'
    | 'thursday'
    | 'friday'
    | 'saturday'
    | 'sunday';
export type RecurrenceMisfirePolicy = 'skip' | 'fire_once' | 'catch_up';

export interface RecurrenceRuleV1 {
    version: 1;
    timezone: string;
    anchor_local: string;
    schedule: {
        freq: RecurrenceFreq;
        interval: number;
        by_weekday: RecurrenceWeekday[];
        by_month_day: number[];
        by_month: number[];
        by_set_pos: number[];
    };
    bounds: {
        start_at_utc: number | null;
        end_at_utc: number | null;
        max_occurrences: number | null;
    };
    execution: {
        misfire_policy: RecurrenceMisfirePolicy;
        max_catchup_runs: number;
    };
}

export interface TaskTemplateDetailItem {
    id: string;
    title: string;
    description: string;
    priority: number;
    timezone: string;
    recurrenceRule: RecurrenceRuleV1;
    recurrenceRuleJson: string;
    nextRunAt: number;
    lastRunAt?: number;
    isActive: boolean;
    projectId: string;
    projectName?: string;
    assignedUserId?: string;
    assignedUserName?: string;
    assignedAgentId?: string;
    assignedAgentName?: string;
    createdAt: number;
    updatedAt: number;
    totalMaterializedTasks: number;
    pendingMaterializedTasks: number;
    runningMaterializedTasks: number;
    completedMaterializedTasks: number;
    failedMaterializedTasks: number;
}

export interface TaskRunDetailItem {
    id: string;
    status: string;
    attemptNumber: number;
    startedAt?: number;
    endedAt?: number;
    createdAt: number;
    durationMs?: number;
    provider?: string;
    model?: string;
    agentId?: string;
    agentName?: string;
    outputResult?: string;
    errorMessage?: string;
    inputTokens: number;
    outputTokens: number;
    totalTokens: number;
    estimatedCostUsd: number;
}

export interface TaskDetailItem extends TaskSummaryItem {
    description: string;
    inputContext?: string;
    outputResult?: string;
    completedAt?: number;
    requestId?: string;
    traceId?: string;
    sessionId?: string;
    latestRun?: TaskRunDetailItem;
    runHistory: TaskRunDetailItem[];
}

export interface AgentStatusItem {
    id: string;
    name: string;
    role: string;
    isActive: boolean;
    runningTasks: number;
    currentTask?: {
        taskId: string;
        taskRunId: string;
        title: string;
        projectName?: string;
        startedAt?: number;
    };
    lastActivityAt?: number;
}

export interface ProjectOverviewItem {
    id: string;
    name: string;
    slug: string;
    status: string;
    taskCounts: StatusCounts;
}

export interface StatusCounts {
    pending: number;
    running: number;
    blocked: number;
    completed: number;
    failed: number;
    canceled: number;
}

export interface SystemMetrics {
    tasks: StatusCounts;
    activeAgents: number;
    totalAgents: number;
    activeProjects: number;
    recentRunsLast24h: number;
    avgRunDurationMs?: number;
    uptimeSeconds: number;
    timestamp: string;
}

export interface BusinessTaskDigestItem {
    taskId: string;
    title: string;
    status: string;
    priority: number;
    projectName?: string;
    agentName?: string;
    dueAt?: number;
    updatedAt: number;
    ageSeconds: number;
}

export interface BusinessRiskItem {
    id: string;
    severity: 'low' | 'medium' | 'high';
    title: string;
    detail: string;
}

export interface BusinessProjectSnapshotItem {
    projectId: string;
    projectName: string;
    totalTasks: number;
    pendingTasks: number;
    runningTasks: number;
    blockedTasks: number;
    completedTasksLastWindow: number;
    failedRunsLastWindow: number;
    llmCostUsdLastWindow: number;
}

export interface BusinessOverviewMetrics {
    windowHours: number;
    timestamp: string;
    throughput: {
        completedTasks: number;
        completedObjectives: number;
        avgLeadTimeHours: number | null;
    };
    reliability: {
        runSuccessRatePct: number;
        firstPassRunSuccessRatePct: number;
        failedRuns: number;
        retriedRuns: number;
    };
    flow: {
        pendingTasks: number;
        runningTasks: number;
        blockedTasks: number;
        blockedWorkRatioPct: number;
        overdueTasks: number;
        dueSoonTasks: number;
        pendingAgeP95Minutes: number;
    };
    capacity: {
        activeAgents: number;
        runningTaskRuns: number;
        maxParallelTaskRuns: number;
        maxTaskRunsPerAgent: number;
        effectiveCapacity: number;
        utilizationPct: number;
    };
    economics: {
        llmCostUsd: number;
        llmTotalTokens: number;
        costPerCompletedTaskUsd: number | null;
        costPerSuccessfulRunUsd: number | null;
    };
    risks: BusinessRiskItem[];
    focus: {
        blockedTasks: BusinessTaskDigestItem[];
        overdueTasks: BusinessTaskDigestItem[];
        failingTasks: BusinessTaskDigestItem[];
    };
    projects: BusinessProjectSnapshotItem[];
}

export interface ObjectiveStepStatusCounts {
    pending: number;
    ready: number;
    queued: number;
    running: number;
    blocked: number;
    completed: number;
    failed: number;
    canceled: number;
}

export interface ObjectiveStepDigestItem {
    stepId: string;
    objectiveId: string;
    objectiveTitle: string;
    title: string;
    stepType: string;
    status: string;
    attemptCount: number;
    maxAttempts: number;
    blockedReason?: string;
    taskId?: string;
    dependsOnStepIds: string[];
    updatedAt: number;
}

export interface ObjectiveOverviewMetrics {
    windowHours: number;
    timestamp: string;
    objectives: {
        total: number;
        active: number;
        blocked: number;
        completedLastWindow: number;
        failedLastWindow: number;
        canceled: number;
    };
    steps: ObjectiveStepStatusCounts & {
        inFlightNonVerify: number;
        readyNonVerify: number;
        maxParallelBudgetActiveObjectives: number;
        parallelUtilizationPct: number;
    };
    blockedSteps: ObjectiveStepDigestItem[];
    failingSteps: ObjectiveStepDigestItem[];
}

export interface OrchestratorMetricGroup {
    key: string;
    count: number;
    p50TotalMs: number;
    p95TotalMs: number;
    p95IntentGateMs: number;
    p95SyncLlmMs: number;
    slaExceededRate: number;
}

export interface OrchestratorLaneRates {
    gateTimeoutRate: number;
    syncAnswerRate: number;
    asyncFallbackRate: number;
}

export interface OrchestratorMetricsDashboard {
    windowHours: number;
    generatedAtIso: string;
    totalEvents: number;
    overall: OrchestratorMetricGroup;
    byChannel: OrchestratorMetricGroup[];
    byMode: OrchestratorMetricGroup[];
    laneRates: OrchestratorLaneRates;
}

export type ObjectiveStatus = 'active' | 'completed' | 'failed' | 'blocked' | 'canceled';
export type ObjectiveStepStatus =
    | 'pending'
    | 'ready'
    | 'queued'
    | 'running'
    | 'completed'
    | 'failed'
    | 'blocked'
    | 'canceled';

export interface ObjectiveSummaryItem {
    id: string;
    projectId: string;
    projectName?: string;
    title: string;
    goal: string;
    status: ObjectiveStatus;
    maxParallelTasks: number;
    createdAt: number;
    updatedAt: number;
    completedAt?: number;
}

export interface ObjectiveStepDetailItem {
    id: string;
    objectiveId: string;
    title: string;
    description: string;
    stepType: string;
    status: ObjectiveStepStatus;
    priority: number;
    verification?: Record<string, unknown>;
    evidence?: Record<string, unknown>;
    taskId?: string;
    attemptCount: number;
    maxAttempts: number;
    createdAt: number;
    updatedAt: number;
    completedAt?: number;
    dependsOnStepIds: string[];
}

export interface ObjectiveDetailItem extends ObjectiveSummaryItem {
    hardConstraints?: Record<string, unknown>;
    successCriteria?: Record<string, unknown>;
    metadata?: Record<string, unknown>;
    projectBrain?: {
        projectId: string;
        contentMarkdown: string;
        version: number;
        metadata?: Record<string, unknown>;
        createdAt: number;
        updatedAt: number;
    };
    steps: ObjectiveStepDetailItem[];
}
