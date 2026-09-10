import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { Radar, LayoutDashboard, Satellite, Activity, ShieldAlert, Users as UsersIcon, LogOut, Map as MapIcon, Eye, Columns2, Globe2, Images, BookOpen } from "lucide-react";
import { api, hasRole } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { LiveBell, CriticalBanner } from "@/components/LiveBell";

const links = [
  { to: "/", label: "Surveillance", icon: LayoutDashboard, id: "nav-dashboard-link" },
  { to: "/ingest", label: "Ingestion", icon: Satellite, id: "nav-ingest-link" },
  { to: "/explorer", label: "Scene Explorer", icon: Globe2, id: "nav-explorer-link" },
  { to: "/events", label: "Events", icon: Images, id: "nav-events-link" },
  { to: "/archive", label: "Archive", icon: BookOpen, id: "nav-archive-link" },
  { to: "/jobs", label: "Jobs & Alerts", icon: Activity, id: "nav-jobs-link" },
  { to: "/zones", label: "Zones", icon: MapIcon, id: "nav-zones-link" },
  { to: "/watchlist", label: "Watchlist", icon: Eye, id: "nav-watchlist-link" },
  { to: "/compare", label: "Compare", icon: Columns2, id: "nav-compare-link" },
];
const ROLE_COLOR = { analyst: "#00F0FF", supervisor: "#FFB703", admin: "#FF2A6D" };

export const Layout = () => {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const [clock, setClock] = useState(new Date());
  const [stats, setStats] = useState(null);
  useEffect(() => {
    const t = setInterval(() => setClock(new Date()), 1000);
    const load = () => api.get("/stats").then((r) => setStats(r.data)).catch(() => {});
    load();
    const s = setInterval(load, 15000);
    return () => { clearInterval(t); clearInterval(s); };
  }, []);

  return (
    <div className="flex h-screen flex-col overflow-hidden text-slate-100" style={{ background: "var(--bg-primary)" }}>
      <header className="flex h-14 shrink-0 items-center gap-6 border-b px-5" style={{ borderColor: "var(--border-default)", background: "rgba(17,24,39,0.85)", backdropFilter: "blur(12px)" }}>
        <NavLink to="/" data-testid="nav-brand" className="flex items-center gap-2.5">
          <span className="grid h-8 w-8 place-items-center rounded-md" style={{ background: "rgba(0,240,255,0.12)", border: "1px solid rgba(0,240,255,0.4)" }}>
            <Radar size={16} color="#00F0FF" />
          </span>
          <span className="whitespace-nowrap font-display text-lg font-bold tracking-tight">Varuna <span style={{ color: "#00F0FF" }}>Netra</span></span>
        </NavLink>
        <nav className="flex items-center gap-1">
          {links.map(({ to, label, icon: Icon, id }) => (
            <NavLink key={to} to={to} end={to === "/"} data-testid={id}
              className={({ isActive }) => `flex items-center gap-2 rounded-md px-3 py-1.5 text-sm transition-colors ${isActive ? "bg-slate-800 text-cyan-300" : "text-slate-400 hover:bg-slate-800/60 hover:text-slate-100"}`}>
              <Icon size={14} /> {label}
            </NavLink>
          ))}
          {hasRole(user, "admin") && (
            <NavLink to="/users" data-testid="nav-users-link" className={({ isActive }) => `flex items-center gap-2 rounded-md px-3 py-1.5 text-sm transition-colors ${isActive ? "bg-slate-800 text-cyan-300" : "text-slate-400 hover:bg-slate-800/60 hover:text-slate-100"}`}>
              <UsersIcon size={14} /> Users
            </NavLink>
          )}
        </nav>
        <div className="ml-auto flex items-center gap-6">
          {stats && (
            <div className="hidden items-center gap-5 md:flex">
              <Stat label="Cases" value={stats.cases_total} testId="nav-stat-cases" />
              <Stat label="Pending" value={stats.pending_review} color="#FFB703" testId="nav-stat-pending" />
              <Stat label="Alerts" value={stats.alerts_unacknowledged} color="#FF2A6D" icon={<ShieldAlert size={12} />} testId="nav-stat-alerts" />
              <Stat label="Jobs" value={stats.jobs_running} color="#00F0FF" testId="nav-stat-jobs" />
            </div>
          )}
          <div className="flex items-center gap-2 font-mono text-xs text-slate-300" data-testid="utc-clock">
            <span className="pulse-dot" />
            {clock.toISOString().replace("T", " ").slice(0, 19)} UTC
          </div>
          <LiveBell />
          {user && (
            <div className="flex items-center gap-2 border-l pl-4" style={{ borderColor: "var(--border-default)" }} data-testid="user-chip">
              <div className="text-right leading-tight">
                <div className="text-xs text-slate-200" data-testid="user-name">{user.name}</div>
                <div className="font-mono text-[10px] uppercase tracking-wider" style={{ color: ROLE_COLOR[user.role] }} data-testid="user-role">{user.role}</div>
              </div>
              <button data-testid="logout-button" onClick={async () => { await logout(); nav("/login"); }} title="Sign out" className="rounded p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-100"><LogOut size={14} /></button>
            </div>
          )}
        </div>
      </header>
      <CriticalBanner />
      <main className="flex-1 overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
};

const Stat = ({ label, value, color = "#F8FAFC", icon, testId }) => (
  <div className="flex items-baseline gap-1.5" data-testid={testId}>
    <span className="label-mono">{label}</span>
    <span className="font-mono text-sm font-semibold flex items-center gap-1" style={{ color }}>{icon}{value}</span>
  </div>
);
