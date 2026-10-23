import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, formatError } from "../api/client";
import { diffLines } from "../format";
import type { Skill, SkillVersion } from "../api/types";

export function SkillHistory({ skill }: { skill: Skill }) {
  const [versions, setVersions] = useState<SkillVersion[]>([]);
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([]);
  const [left, setLeft] = useState("");
  const [right, setRight] = useState("");
  const [leftText, setLeftText] = useState("");
  const [rightText, setRightText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      api.listSkillVersions(skill.slug),
      api.listSkillProjects(skill.slug),
    ])
      .then(([vers, used]) => {
        if (cancelled) return;
        setVersions(vers);
        setProjects(used);
        const newest = vers[0]?.version ?? "";
        const prev = vers[1]?.version ?? newest;
        setRight(newest);
        setLeft(prev);
      })
      .catch((e) => {
        if (!cancelled) setError(formatError(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [skill.slug]);

  useEffect(() => {
    if (!left) return;
    api
      .getSkillContent(skill.slug, left)
      .then((r) => setLeftText(r.content))
      .catch((e) => setError(formatError(e)));
  }, [skill.slug, left]);

  useEffect(() => {
    if (!right) return;
    api
      .getSkillContent(skill.slug, right)
      .then((r) => setRightText(r.content))
      .catch((e) => setError(formatError(e)));
  }, [skill.slug, right]);

  if (loading) return <p className="muted small">Loading versions…</p>;
  if (error) return <p className="error-text">{error}</p>;

  const lines =
    left === right
      ? rightText.split("\n").map((text) => ({ kind: "same" as const, text }))
      : diffLines(leftText, rightText);

  return (
    <div>
      <p className="sk-history-meta small muted">
        Used by{" "}
        {projects.length === 0
          ? "no projects yet"
          : projects.map((p, i) => (
              <span key={p.id}>
                {i > 0 && ", "}
                <Link to={`/projects/${p.id}`}>{p.name}</Link>
              </span>
            ))}
        .
      </p>
      {versions.length === 0 ? (
        <p className="muted small">No published versions.</p>
      ) : (
        <>
          <div className="sk-compare-row">
            <label>
              Compare
              <select value={left} onChange={(e) => setLeft(e.target.value)}>
                {versions.map((v) => (
                  <option key={`l-${v.version}`} value={v.version}>
                    {v.version}
                    {v.version === skill.active_version ? " (active)" : ""}
                  </option>
                ))}
              </select>
            </label>
            <label>
              With
              <select value={right} onChange={(e) => setRight(e.target.value)}>
                {versions.map((v) => (
                  <option key={`r-${v.version}`} value={v.version}>
                    {v.version}
                    {v.version === skill.active_version ? " (active)" : ""}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <pre className="sk-diff-pre">
            {lines.map((line, i) => (
              <div key={i} className={`sk-diff-${line.kind}`}>
                {line.kind === "add" ? "+ " : line.kind === "del" ? "- " : "  "}
                {line.text}
              </div>
            ))}
          </pre>
        </>
      )}
    </div>
  );
}
