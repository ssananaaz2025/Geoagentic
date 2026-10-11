import React from "react";

const ROLES = [
  { hash: "#/control-room", label: "Control Room", icon: "🚨" },
  { hash: "#/driver", label: "Driver", icon: "🚑" },
  { hash: "#/admin", label: "Admin", icon: "🛠" },
];

/**
 * Three-way role switcher shown in every dashboard's topbar. This is a
 * demo-only convenience (no real auth/accounts) — in production each
 * dashboard would live behind its own login and this component would go
 * away entirely.
 */
export default function RoleSwitch({ active }) {
  return (
    <div className="role-switch">
      {ROLES.map((r) => (
        <a
          key={r.hash}
          href={r.hash}
          className={`role-link ${active === r.hash ? "active" : ""}`}
        >
          <span className="role-icon">{r.icon}</span>
          {r.label}
        </a>
      ))}
    </div>
  );
}
