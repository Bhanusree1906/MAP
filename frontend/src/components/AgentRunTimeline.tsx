import type { AgentRun } from "../api/types";
import { formatLocalDateTime } from "../format";
import { StatusBadge } from "./StatusBadge";

export function AgentRunTimeline({ agentRuns }: { agentRuns: AgentRun[] }) {
  if (agentRuns.length === 0)
    return <p className="muted">No agent runs yet.</p>;

  return (
    <ul className="timeline">
      {agentRuns.map((run) => (
        <li key={run.id}>
          <div className="timeline-row">
            <strong>{run.agent_name}</strong>
            <StatusBadge status={run.status} />
          </div>
          <div className="muted small">
            {run.stage}
            {run.started_at &&
              ` · started ${formatLocalDateTime(run.started_at)}`}
            {run.completed_at &&
              ` · completed ${formatLocalDateTime(run.completed_at)}`}
          </div>
          {run.error_message && (
            <div className="error-text">{run.error_message}</div>
          )}
        </li>
      ))}
    </ul>
  );
}
