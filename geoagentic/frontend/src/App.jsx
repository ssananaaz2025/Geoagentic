import React, { useEffect, useState } from "react";
import LandingSelect from "./dashboards/LandingSelect.jsx";
import ControlRoomDashboard from "./dashboards/ControlRoomDashboard.jsx";
import DriverDashboard from "./dashboards/DriverDashboard.jsx";
import AdminDashboard from "./dashboards/AdminDashboard.jsx";

function routeFromHash() {
  const h = window.location.hash || "";
  if (h.startsWith("#/control-room")) return "control-room";
  if (h.startsWith("#/driver")) return "driver";
  if (h.startsWith("#/admin")) return "admin";
  return "landing";
}

/**
 * Lightweight hash router across the three dashboards. No routing library
 * is used on purpose - three flat routes don't need one. Each dashboard
 * renders its own topbar (with the shared RoleSwitch) and manages its own
 * state via useGeoState.
 */
export default function App() {
  const [route, setRoute] = useState(routeFromHash());

  useEffect(() => {
    const onHashChange = () => setRoute(routeFromHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  if (route === "control-room") return <ControlRoomDashboard />;
  if (route === "driver") return <DriverDashboard />;
  if (route === "admin") return <AdminDashboard />;
  return <LandingSelect />;
}
