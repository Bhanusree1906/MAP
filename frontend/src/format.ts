export function formatUsd(value: string | number | null | undefined): string {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (Number.isNaN(n)) return "—";
  return `$${n.toFixed(4)}`;
}

/** Backend stores naive-UTC datetimes (datetime.utcnow()), so a serialized
 * value without a timezone suffix must be read as UTC - otherwise `new Date()`
 * treats it as local time and the duration drifts by the machine's UTC offset.
 * Mirrors the same convention used by formatLocalDateTime below. */
function parseTimestamp(value: string): number {
  const iso = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  return new Date(iso).getTime();
}

export function formatDuration(
  startedAt: string | null,
  completedAt: string | null,
): string {
  if (!startedAt) return "—";
  const start = parseTimestamp(startedAt);
  if (Number.isNaN(start)) return "—";
  const end = completedAt ? parseTimestamp(completedAt) : Date.now();
  if (Number.isNaN(end)) return "—";
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rem = seconds % 60;
  if (minutes < 60) return rem ? `${minutes}m ${rem}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

export function formatLocalDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const iso = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function extractChangedFiles(summary: string): string[] {
  const found = new Set<string>();
  for (const match of summary.matchAll(/`([^`\n]+?\.[A-Za-z][A-Za-z0-9]*)`/g)) {
    found.add(match[1]);
  }
  for (const match of summary.matchAll(
    /^\s*[-*]\s+([\w./-]+\.[A-Za-z][\w]*)/gm,
  )) {
    found.add(match[1]);
  }
  return [...found].slice(0, 24);
}

export type DiffLine = { kind: "same" | "add" | "del"; text: string };

export function diffLines(before: string, after: string): DiffLine[] {
  const a = before.split("\n");
  const b = after.split("\n");
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      out.push({ kind: "same", text: a[i] });
      i += 1;
      j += 1;
      continue;
    }
    const ahead = j < b.length ? a.indexOf(b[j], i) : -1;
    const back = i < a.length ? b.indexOf(a[i], j) : -1;
    if (
      j < b.length &&
      (ahead === i || (ahead !== -1 && (back === -1 || ahead - i <= back - j)))
    ) {
      if (ahead > i) {
        while (i < ahead) {
          out.push({ kind: "del", text: a[i] });
          i += 1;
        }
      } else {
        out.push({ kind: "add", text: b[j] });
        j += 1;
      }
    } else if (i < a.length) {
      out.push({ kind: "del", text: a[i] });
      i += 1;
    } else if (j < b.length) {
      out.push({ kind: "add", text: b[j] });
      j += 1;
    }
  }
  return out;
}
