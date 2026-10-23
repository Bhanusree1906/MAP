import type { CostLog } from "../api/types";

export function CostSummary({
  costLogs,
  totalCost,
}: {
  costLogs: CostLog[];
  totalCost: string;
}) {
  return (
    <div className="card">
      <h4>Cost</h4>
      <p>
        Total: <strong>${Number(totalCost).toFixed(4)}</strong>
      </p>
      {costLogs.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Agent</th>
              <th>Model</th>
              <th>Tokens (in/out)</th>
              <th>Cost</th>
            </tr>
          </thead>
          <tbody>
            {costLogs.map((log) => (
              <tr key={log.id}>
                <td>{log.agent_name}</td>
                <td>{log.model}</td>
                <td>
                  {log.input_tokens} / {log.output_tokens}
                </td>
                <td>${Number(log.total_cost).toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
