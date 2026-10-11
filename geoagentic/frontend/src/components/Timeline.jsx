import React from "react";
import { useNow } from "../useNow";
import { ago, clockTime } from "../time";

const ICON = {
  dispatch: "🚨", arrival: "✅", system: "ℹ️", accident: "💥", traffic: "🚦", roadblock: "🚧",
  flood: "🌊", hazard: "🔥", recommendation: "💡", reroute: "↪️", deviation: "📡",
};

/** Event log. Re-renders every second so "x s ago" really counts up, and
 * ages are measured against the server's clock (see time.js). */
export default function Timeline({ events, limit = 12 }) {
  useNow(1000);
  const shown = events.slice(0, limit);
  return (
    <div>
      <div className="pane-header">WHAT'S HAPPENING</div>
      {shown.length === 0 && <div className="empty-state">No events yet.</div>}
      {shown.map((e) => (
        <div key={e.id} className={`timeline-item evt-${e.event_type}`}>
          <div className="t-time">
            <span>{clockTime(e.timestamp)}</span>
            <span className="t-ago">{ago(e.timestamp)}</span>
          </div>
          <div className="t-msg">{ICON[e.event_type] ? `${ICON[e.event_type]} ` : ""}{e.message}</div>
        </div>
      ))}
    </div>
  );
}
