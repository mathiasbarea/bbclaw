import { SpotlightCard } from './SpotlightCard';
import { ObjectiveDagPanel } from './ObjectiveDagPanel';
import type {
    BusinessOverviewMetrics,
    ObjectiveOverviewMetrics,
    OrchestratorMetricsDashboard,
    BusinessTaskDigestItem,
    ObjectiveDetailItem,
    ObjectiveSummaryItem,
} from '../types';

function formatPct(value: number): string {
    if (!Number.isFinite(value)) return '0%';
    return `${value.toFixed(1)}%`;
}

function formatUsd(value: number | null | undefined): string {
    if (typeof value !== 'number' || !Number.isFinite(value)) return 'N/A';
    return `$${value.toFixed(4)}`;
}

function formatCompactNumber(value: number): string {
    return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(value);
}

function severityClass(severity: 'low' | 'medium' | 'high'): string {
    if (severity === 'high') return 'severity-high';
    if (severity === 'medium') return 'severity-medium';
    return 'severity-low';
}

function TaskList({
    title,
    tasks,
    onOpenTask,
}: {
    title: string;
    tasks: BusinessTaskDigestItem[];
    onOpenTask: (taskId: string) => void;
}) {
    return (
        <div className="business-list-block">
            <h4>{title}</h4>
            {tasks.length === 0 ? (
                <p className="business-list-empty">No items.</p>
            ) : (
                <div className="business-task-list">
                    {tasks.map((task) => (
                        <button
                            key={`${title}:${task.taskId}`}
                            type="button"
                            className="business-task-item"
                            onClick={() => onOpenTask(task.taskId)}
                        >
                            <span className="business-task-item-title">{task.title}</span>
                            <span className="business-task-item-meta">
                                {task.projectName || 'General'} • {task.status}
                            </span>
                        </button>
                    ))}
                </div>
            )}
        </div>
    );
}

export function BusinessOverviewPanel({
    businessMetrics,
    objectiveMetrics,
    orchestratorMetrics,
    objectives,
    selectedObjectiveId,
    objectiveDetail,
    isObjectiveLoading,
    onSelectObjective,
    onOpenTask,
}: {
    businessMetrics: BusinessOverviewMetrics | null;
    objectiveMetrics: ObjectiveOverviewMetrics | null;
    orchestratorMetrics: OrchestratorMetricsDashboard | null;
    objectives: ObjectiveSummaryItem[];
    selectedObjectiveId: string | null;
    objectiveDetail: ObjectiveDetailItem | null;
    isObjectiveLoading: boolean;
    onSelectObjective: (objectiveId: string) => void;
    onOpenTask: (taskId: string) => void;
}) {
    if (!businessMetrics) {
        return (
            <div className="business-view-shell glass-panel">
                <p className="business-loading">Loading business overview...</p>
            </div>
        );
    }

    const kpis = [
        {
            label: 'Completed Tasks',
            value: String(businessMetrics.throughput.completedTasks),
            hint: `last ${businessMetrics.windowHours}h`,
        },
        {
            label: 'Run Success',
            value: formatPct(businessMetrics.reliability.runSuccessRatePct),
            hint: `${businessMetrics.reliability.failedRuns} failed runs`,
        },
        {
            label: 'First-Pass Success',
            value: formatPct(businessMetrics.reliability.firstPassRunSuccessRatePct),
            hint: `${businessMetrics.reliability.retriedRuns} retried runs`,
        },
        {
            label: 'Blocked Work',
            value: `${businessMetrics.flow.blockedTasks}`,
            hint: `${formatPct(businessMetrics.flow.blockedWorkRatioPct)} of active flow`,
        },
        {
            label: 'Capacity Usage',
            value: formatPct(businessMetrics.capacity.utilizationPct),
            hint: `${businessMetrics.capacity.runningTaskRuns}/${businessMetrics.capacity.effectiveCapacity || 0} run slots`,
        },
        {
            label: 'LLM Cost',
            value: formatUsd(businessMetrics.economics.llmCostUsd),
            hint: `cost/task ${formatUsd(businessMetrics.economics.costPerCompletedTaskUsd)}`,
        },
    ];

    return (
        <div className="business-view-shell">
            <div className="business-kpi-grid">
                {kpis.map((kpi) => (
                    <SpotlightCard key={kpi.label} className="business-kpi-card" activeColor="rgba(59, 130, 246, 0.16)">
                        <span className="business-kpi-label">{kpi.label}</span>
                        <strong className="business-kpi-value">{kpi.value}</strong>
                        <span className="business-kpi-hint">{kpi.hint}</span>
                    </SpotlightCard>
                ))}
            </div>

            <div className="business-content-grid">
                <div className="business-column glass-panel">
                    <h3 className="business-section-title">Risk Radar</h3>
                    <div className="business-risk-list">
                        {businessMetrics.risks.map((risk) => (
                            <div key={risk.id} className={`business-risk-item ${severityClass(risk.severity)}`}>
                                <div className="business-risk-title-row">
                                    <span className={`risk-pill ${severityClass(risk.severity)}`}>{risk.severity}</span>
                                    <strong>{risk.title}</strong>
                                </div>
                                <p>{risk.detail}</p>
                            </div>
                        ))}
                    </div>

                    <div className="business-objective-panel">
                        <h4>Objective Flow</h4>
                        {objectiveMetrics ? (
                            <>
                                <div className="business-objective-stats">
                                    <span>Active {objectiveMetrics.objectives.active}</span>
                                    <span>Blocked {objectiveMetrics.objectives.blocked}</span>
                                    <span>In-flight {objectiveMetrics.steps.inFlightNonVerify}</span>
                                    <span>Parallel {formatPct(objectiveMetrics.steps.parallelUtilizationPct)}</span>
                                </div>
                                {objectiveMetrics.blockedSteps.length === 0 ? (
                                    <p className="business-list-empty">No blocked objective steps.</p>
                                ) : (
                                    <div className="business-objective-list">
                                        {objectiveMetrics.blockedSteps.map((step) => (
                                            <div key={step.stepId} className="business-objective-item">
                                                <strong>{step.title}</strong>
                                                <span>{step.objectiveTitle}</span>
                                                <span>{step.blockedReason || 'blocked'}</span>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </>
                        ) : (
                            <p className="business-list-empty">Objective metrics unavailable.</p>
                        )}
                    </div>

                    <ObjectiveDagPanel
                        objectives={objectives}
                        selectedObjectiveId={selectedObjectiveId}
                        objectiveDetail={objectiveDetail}
                        isLoading={isObjectiveLoading}
                        onSelectObjective={onSelectObjective}
                        onOpenTask={onOpenTask}
                    />
                </div>

                <div className="business-column glass-panel">
                    <h3 className="business-section-title">Action Queue</h3>
                    <TaskList title="Blocked Tasks" tasks={businessMetrics.focus.blockedTasks} onOpenTask={onOpenTask} />
                    <TaskList title="Overdue Tasks" tasks={businessMetrics.focus.overdueTasks} onOpenTask={onOpenTask} />
                    <TaskList title="Failing Tasks" tasks={businessMetrics.focus.failingTasks} onOpenTask={onOpenTask} />
                </div>

                <div className="business-column glass-panel">
                    <h3 className="business-section-title">Economics & SLA</h3>
                    <div className="business-mini-grid">
                        <div className="business-mini-card">
                            <span>Tokens</span>
                            <strong>{formatCompactNumber(businessMetrics.economics.llmTotalTokens)}</strong>
                        </div>
                        <div className="business-mini-card">
                            <span>Pending p95 age</span>
                            <strong>{Math.round(businessMetrics.flow.pendingAgeP95Minutes)}m</strong>
                        </div>
                        <div className="business-mini-card">
                            <span>Overdue</span>
                            <strong>{businessMetrics.flow.overdueTasks}</strong>
                        </div>
                        <div className="business-mini-card">
                            <span>Due soon</span>
                            <strong>{businessMetrics.flow.dueSoonTasks}</strong>
                        </div>
                    </div>

                    {orchestratorMetrics ? (
                        <div className="business-orchestrator-panel">
                            <h4>Orchestrator Lane</h4>
                            <div className="business-objective-stats">
                                <span>Sync {formatPct(orchestratorMetrics.laneRates.syncAnswerRate)}</span>
                                <span>Async fallback {formatPct(orchestratorMetrics.laneRates.asyncFallbackRate)}</span>
                                <span>Gate timeout {formatPct(orchestratorMetrics.laneRates.gateTimeoutRate)}</span>
                                <span>p95 {orchestratorMetrics.overall.p95TotalMs}ms</span>
                            </div>
                        </div>
                    ) : null}

                    <div className="business-project-panel">
                        <h4>Top Projects</h4>
                        {businessMetrics.projects.length === 0 ? (
                            <p className="business-list-empty">No project activity.</p>
                        ) : (
                            <div className="business-project-list">
                                {businessMetrics.projects.slice(0, 8).map((project) => (
                                    <div key={project.projectId} className="business-project-item">
                                        <strong>{project.projectName}</strong>
                                        <span>
                                            tasks {project.totalTasks} • blocked {project.blockedTasks} • cost {formatUsd(project.llmCostUsdLastWindow)}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}
