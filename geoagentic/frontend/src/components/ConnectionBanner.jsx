import React from "react";

/** Shown only when the backend can't be reached. Polling continues in the
 * background, so the banner disappears by itself once the server is back. */
export default function ConnectionBanner({ online }) {
  if (online) return null;
  return (
    <div className="conn-banner" role="alert">
      <b>Backend offline.</b> The screen can't reach the server, so nothing will update.
      Start it with <code>cd backend &amp;&amp; uvicorn app.main:app --port 8000</code> — this
      page reconnects automatically.
    </div>
  );
}
