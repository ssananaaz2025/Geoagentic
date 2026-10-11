import React from "react";

const CARDS = [
  {
    hash: "#/control-room",
    icon: "🚨",
    title: "Control Room",
    tag: "Dispatch / Police",
    desc: "Dispatch vehicles, inject scenarios, review GeoAgent recommendations, and approve or dismiss reroutes. The full multi-vehicle command view.",
  },
  {
    hash: "#/driver",
    icon: "🚑",
    title: "Driver",
    tag: "In-vehicle",
    desc: "A focused single-vehicle view: current mission, live ETA, route, and any pending GeoAgent recommendation — plus a way to report GPS drift.",
  },
  {
    hash: "#/admin",
    icon: "🛠",
    title: "Admin",
    tag: "Fleet & system",
    desc: "Fleet roster and stats, add vehicles, check whether the Google Maps and Claude LLM integrations are live, and reset the demo.",
  },
];

export default function LandingSelect() {
  return (
    <div className="landing-shell">
      <div className="landing-brand">
        <div className="brand-mark" />
        <span className="brand-name">GEOAGENTIC</span>
      </div>
      <div className="landing-sub">emergency vehicle control room — choose a dashboard</div>

      <div className="landing-grid">
        {CARDS.map((c) => (
          <a key={c.hash} href={c.hash} className="landing-card">
            <div className="landing-card-icon">{c.icon}</div>
            <div className="landing-card-title">{c.title}</div>
            <div className="landing-card-tag">{c.tag}</div>
            <div className="landing-card-desc">{c.desc}</div>
            <div className="landing-card-cta">Open →</div>
          </a>
        ))}
      </div>

      <div className="landing-footnote">
        Demo-only role switcher — no login required. In production each
        dashboard would sit behind its own authentication.
      </div>
    </div>
  );
}
