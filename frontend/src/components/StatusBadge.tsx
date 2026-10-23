import type { PipelineRunStatus } from "../api/types";

export function StatusBadge({
  status,
}: {
  status: PipelineRunStatus | string;
}) {
  return (
    <span className={`status-badge status-${status.replace(/_/g, "-")}`}>
      {status.replace(/_/g, " ")}
    </span>
  );
}
