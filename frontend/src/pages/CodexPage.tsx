import { Navigate } from "react-router-dom";

/** Codex now lives on the unified Connections page. */
export function CodexPage() {
  return <Navigate to="/connections#codex" replace />;
}
