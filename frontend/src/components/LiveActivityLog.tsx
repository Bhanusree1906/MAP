import { useEffect, useRef, useState } from "react";
import type { AgentRunEvent } from "../api/types";

const DISCRETE_EVENT_KINDS = ["step", "timing"];

interface EventGroup {
  key: number;
  kind: AgentRunEvent["kind"];
  content: string;
}

function groupEvents(events: AgentRunEvent[]): EventGroup[] {
  const groups: EventGroup[] = [];
  for (const event of events) {
    const last = groups[groups.length - 1];
    if (
      last &&
      last.kind === event.kind &&
      !DISCRETE_EVENT_KINDS.includes(event.kind)
    ) {
      last.content += event.content;
    } else {
      groups.push({ key: event.seq, kind: event.kind, content: event.content });
    }
  }
  return groups;
}

export function LiveActivityLog({
  title,
  events,
  isLive,
}: {
  title: string;
  events: AgentRunEvent[];
  isLive: boolean;
}) {
  const logRef = useRef<HTMLDivElement>(null);
  const [stuckToBottom, setStuckToBottom] = useState(true);

  useEffect(() => {
    if (!stuckToBottom) return;
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [events, stuckToBottom]);

  return (
    <div style={{ marginTop: 16 }}>
      <div className="card-head">
        <h3>{title}</h3>
        {!stuckToBottom && isLive && (
          <button
            type="button"
            className="btn-secondary"
            onClick={() => {
              setStuckToBottom(true);
              logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
            }}
          >
            Jump to latest
          </button>
        )}
      </div>
      {events.length === 0 && (
        <p className="muted">Waiting for the first step…</p>
      )}
      <div
        ref={logRef}
        className="live-log"
        onScroll={(e) => {
          const el = e.currentTarget;
          const nearBottom =
            el.scrollHeight - el.scrollTop - el.clientHeight < 40;
          setStuckToBottom(nearBottom);
        }}
      >
        {groupEvents(events).map((g) => (
          <div
            key={g.key}
            className={
              g.kind === "step"
                ? "live-step"
                : g.kind === "timing"
                  ? "live-timing"
                  : "live-engine"
            }
          >
            {g.content}
          </div>
        ))}
      </div>
    </div>
  );
}
