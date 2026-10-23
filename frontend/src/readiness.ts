export function readinessHref(projectId: string, issue: string): string {
  if (/org catalog/i.test(issue)) return `/projects/${projectId}#org-catalog`;
  if (/git access token/i.test(issue)) return "/connections#github";
  if (/auth URL/i.test(issue)) return "/connections#salesforce";
  if (/Salesforce/i.test(issue)) return `/projects/${projectId}#settings`;
  if (/Jira/i.test(issue)) return `/projects/${projectId}#settings`;
  if (/repository URL/i.test(issue)) return `/projects/${projectId}#settings`;
  return `/projects/${projectId}#settings`;
}

export function splitRiskItems(risks: string): string[] {
  const lines = risks
    .split(/\n/)
    .map((line) =>
      line
        .replace(/^\s*[-*]\s+/, "")
        .replace(/^\s*\d+\.\s+/, "")
        .trim(),
    )
    .filter(Boolean);
  if (lines.length <= 1) return risks.trim() ? [risks.trim()] : [];
  return lines;
}
