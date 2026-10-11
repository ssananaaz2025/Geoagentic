/**
 * Shared clock helpers.
 *
 * The backend stamps timeline events with ITS clock. If the browser clock
 * differs (other machine, wrong timezone setting, VM drift) "x s ago" labels
 * come out frozen at 0s or negative. The poller records the offset between
 * the two clocks on every snapshot so everything is computed on server time.
 */
let offsetSec = 0;     // serverTime - browserTime
let fetchedAtMs = 0;   // browser time of the last good snapshot

export function recordSnapshot(serverTimeSec) {
  fetchedAtMs = Date.now();
  if (typeof serverTimeSec === "number") offsetSec = serverTimeSec - Date.now() / 1000;
}
export const serverNowSec = () => Date.now() / 1000 + offsetSec;
export const secondsSinceSnapshot = () => (fetchedAtMs ? (Date.now() - fetchedAtMs) / 1000 : 0);

export function ago(tsSec) {
  const s = Math.max(0, Math.floor(serverNowSec() - tsSec));
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  return `${Math.floor(m / 60)}h ago`;
}

export function clockTime(tsSec, withSeconds = true) {
  const d = new Date((tsSec - offsetSec) * 1000);  // back to browser-local time
  return d.toLocaleTimeString([], withSeconds
    ? { hour: "2-digit", minute: "2-digit", second: "2-digit" }
    : { hour: "2-digit", minute: "2-digit" });
}
