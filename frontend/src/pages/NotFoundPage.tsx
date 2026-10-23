import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <div className="card empty-state">
      <h2>Page not found</h2>
      <p>That URL is not a project, run, or setup screen.</p>
      <div className="btn-row">
        <Link className="btn" to="/">
          Projects
        </Link>
        <Link className="btn-secondary btn" to="/runs">
          Runs
        </Link>
      </div>
    </div>
  );
}
