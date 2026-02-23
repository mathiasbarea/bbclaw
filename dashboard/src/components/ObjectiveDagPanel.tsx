import type { ObjectiveDetailItem, ObjectiveStepDetailItem, ObjectiveSummaryItem } from '../types';

interface DagNodeMeta {
    step: ObjectiveStepDetailItem;
    level: number;
}

function formatRelativeAge(epochSeconds?: number): string {
    if (!epochSeconds || !Number.isFinite(epochSeconds)) return 'N/A';
    const delta = Math.max(0, Math.floor(Date.now() / 1000) - epochSeconds);
    if (delta < 60) return 'just now';
    if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
    if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
    return `${Math.floor(delta / 86400)}d ago`;
}

function statusClass(status: ObjectiveStepDetailItem['status']): string {
    return `objective-step-${status}`;
}

function buildAdjacency(steps: ObjectiveStepDetailItem[]): Map<string, string[]> {
    const byId = new Set(steps.map((step) => step.id));
    const adjacency = new Map<string, string[]>();
    for (const step of steps) adjacency.set(step.id, []);
    for (const step of steps) {
        for (const depId of step.dependsOnStepIds || []) {
            if (!byId.has(depId)) continue;
            const bucket = adjacency.get(depId) || [];
            bucket.push(step.id);
            adjacency.set(depId, bucket);
        }
    }
    return adjacency;
}

function computeLevels(steps: ObjectiveStepDetailItem[]): DagNodeMeta[] {
    const byId = new Map(steps.map((step) => [step.id, step]));
    const adjacency = buildAdjacency(steps);
    const indegree = new Map<string, number>();
    const level = new Map<string, number>();

    for (const step of steps) {
        const validDeps = (step.dependsOnStepIds || []).filter((depId) => byId.has(depId));
        indegree.set(step.id, validDeps.length);
        if (validDeps.length === 0) level.set(step.id, 0);
    }

    const queue: string[] = steps.filter((step) => (indegree.get(step.id) || 0) === 0).map((step) => step.id);
    while (queue.length > 0) {
        const current = queue.shift() as string;
        const currentLevel = level.get(current) || 0;
        for (const next of adjacency.get(current) || []) {
            const nextDegree = (indegree.get(next) || 0) - 1;
            indegree.set(next, nextDegree);
            level.set(next, Math.max(level.get(next) || 0, currentLevel + 1));
            if (nextDegree === 0) {
                queue.push(next);
            }
        }
    }

    // Safety fallback for cycle/invalid dependency remnants.
    let maxKnownLevel = 0;
    for (const value of level.values()) {
        if (value > maxKnownLevel) maxKnownLevel = value;
    }
    for (const step of steps) {
        if (!level.has(step.id)) {
            maxKnownLevel += 1;
            level.set(step.id, maxKnownLevel);
        }
    }

    return steps
        .map((step) => ({
            step,
            level: level.get(step.id) || 0,
        }))
        .sort((a, b) => a.level - b.level || a.step.priority - b.step.priority || a.step.createdAt - b.step.createdAt);
}

function computeCriticalPathStepIds(steps: ObjectiveStepDetailItem[]): Set<string> {
    const byId = new Map(steps.map((step) => [step.id, step]));
    const nodes = computeLevels(steps).map((item) => item.step.id);
    const distance = new Map<string, number>();
    const predecessor = new Map<string, string | null>();

    for (const nodeId of nodes) {
        const step = byId.get(nodeId);
        if (!step) continue;
        const deps = (step.dependsOnStepIds || []).filter((depId) => byId.has(depId));
        if (deps.length === 0) {
            distance.set(nodeId, 1);
            predecessor.set(nodeId, null);
            continue;
        }

        let bestDep: string | null = null;
        let bestScore = 0;
        for (const dep of deps) {
            const score = distance.get(dep) || 1;
            if (score > bestScore) {
                bestScore = score;
                bestDep = dep;
            }
        }
        distance.set(nodeId, bestScore + 1);
        predecessor.set(nodeId, bestDep);
    }

    let lastNode: string | null = null;
    let maxDistance = 0;
    for (const [nodeId, score] of distance.entries()) {
        if (score > maxDistance) {
            maxDistance = score;
            lastNode = nodeId;
        }
    }

    const critical = new Set<string>();
    while (lastNode) {
        critical.add(lastNode);
        lastNode = predecessor.get(lastNode) || null;
    }
    return critical;
}

function parseBlockedReason(step: ObjectiveStepDetailItem): string | null {
    const evidence = step.evidence;
    if (!evidence || typeof evidence !== 'object' || Array.isArray(evidence)) return null;
    const raw = evidence.blocked_reason;
    return typeof raw === 'string' && raw.trim() ? raw.trim() : null;
}

export function ObjectiveDagPanel({
    objectives,
    selectedObjectiveId,
    objectiveDetail,
    isLoading,
    onSelectObjective,
    onOpenTask,
}: {
    objectives: ObjectiveSummaryItem[];
    selectedObjectiveId: string | null;
    objectiveDetail: ObjectiveDetailItem | null;
    isLoading: boolean;
    onSelectObjective: (objectiveId: string) => void;
    onOpenTask: (taskId: string) => void;
}) {
    const dag = objectiveDetail ? computeLevels(objectiveDetail.steps) : [];
    const grouped = new Map<number, ObjectiveStepDetailItem[]>();
    for (const item of dag) {
        const bucket = grouped.get(item.level) || [];
        bucket.push(item.step);
        grouped.set(item.level, bucket);
    }
    const levels = [...grouped.keys()].sort((a, b) => a - b);
    const criticalPathIds = objectiveDetail ? computeCriticalPathStepIds(objectiveDetail.steps) : new Set<string>();

    const selected = objectives.find((objective) => objective.id === selectedObjectiveId) || null;
    const activeCount = objectives.filter((objective) => objective.status === 'active').length;

    return (
        <div className="objective-dag-shell">
            <div className="objective-dag-header">
                <div>
                    <h4>Objective DAG</h4>
                    <p>{activeCount} active objective(s)</p>
                </div>
                <select
                    className="objective-dag-select"
                    value={selectedObjectiveId || ''}
                    onChange={(event) => {
                        const next = event.target.value;
                        if (!next) return;
                        onSelectObjective(next);
                    }}
                >
                    {objectives.length === 0 ? <option value="">No objectives</option> : null}
                    {objectives.map((objective) => (
                        <option key={objective.id} value={objective.id}>
                            [{objective.status}] {objective.title}
                        </option>
                    ))}
                </select>
            </div>

            {selected ? (
                <div className="objective-dag-summary-row">
                    <span>{selected.projectName || 'General project'}</span>
                    <span>parallel cap {selected.maxParallelTasks}</span>
                    <span>updated {formatRelativeAge(selected.updatedAt)}</span>
                </div>
            ) : null}

            {isLoading ? <p className="business-list-empty">Loading objective graph...</p> : null}

            {!isLoading && objectiveDetail && objectiveDetail.steps.length === 0 ? (
                <p className="business-list-empty">Objective has no steps.</p>
            ) : null}

            {!isLoading && !objectiveDetail ? <p className="business-list-empty">Select an objective.</p> : null}

            {!isLoading && objectiveDetail && objectiveDetail.steps.length > 0 ? (
                <div className="objective-dag-levels">
                    {levels.map((level) => (
                        <div key={`level:${level}`} className="objective-dag-level">
                            <div className="objective-dag-level-title">Stage {level + 1}</div>
                            <div className="objective-dag-level-nodes">
                                {(grouped.get(level) || []).map((step) => {
                                    const blockedReason = parseBlockedReason(step);
                                    const isCritical = criticalPathIds.has(step.id);
                                    return (
                                        <div
                                            key={step.id}
                                            className={`objective-step-card ${statusClass(step.status)}${isCritical ? ' critical' : ''}`}
                                        >
                                            <div className="objective-step-top">
                                                <span className="objective-step-type">{step.stepType}</span>
                                                <span className="objective-step-status">{step.status}</span>
                                            </div>
                                            <strong className="objective-step-title">{step.title}</strong>
                                            <p className="objective-step-meta">
                                                attempts {step.attemptCount}/{step.maxAttempts} | deps {step.dependsOnStepIds.length}
                                            </p>
                                            {blockedReason ? (
                                                <p className="objective-step-blocked">blocked: {blockedReason}</p>
                                            ) : null}
                                            {step.taskId ? (
                                                <button
                                                    type="button"
                                                    className="objective-step-task-btn"
                                                    onClick={() => onOpenTask(step.taskId as string)}
                                                >
                                                    Open linked task
                                                </button>
                                            ) : null}
                                        </div>
                                    );
                                })}
                            </div>
                        </div>
                    ))}
                </div>
            ) : null}
        </div>
    );
}
