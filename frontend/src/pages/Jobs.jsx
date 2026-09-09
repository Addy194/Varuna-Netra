import { Fragment, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Check, RefreshCw } from "lucide-react";
import { api, apiError, fmtTime, hasRole } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

const STATUS_COLOR = { queued: "#94A3B8", running: "#00F0FF", succeeded: "#10B981", failed: "#FF2A6D" };

export default function Jobs() {
  const { user } = useAuth();
  const [jobs, setJobs] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [open, setOpen] = useState(null);
  const nav = useNavigate();
  const load = () => Promise.all([api.get("/jobs"), api.get("/alerts")]).then(([j, a]) => { setJobs(j.data); setAlerts(a.data); }).catch((e) => toast.error(e.message));
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, []);
  const ack = async (id) => { try { await api.post(`/alerts/${id}/ack`); toast.success("Alert acknowledged"); load(); } catch (e) { toast.error(apiError(e)); } };

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mb-6 flex items-end justify-between">
        <div>
          <p className="label-mono mb-1">Background workers · retries · alerts</p>
          <h1 className="font-display text-3xl font-extrabold tracking-tight sm:text-4xl">Jobs & Alerts</h1>
        </div>
        <button data-testid="btn-refresh-jobs" onClick={load} className="inline-flex items-center gap-1.5 rounded border px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider text-slate-300 hover:text-white" style={{ borderColor: "var(--border-highlight)" }}><RefreshCw size={12} /> Refresh</button>
      </div>
      <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
        <div className="panel overflow-hidden" data-testid="jobs-list">
          <table className="w-full text-xs">
            <thead><tr className="label-mono text-left">{["Job", "Type", "Status", "Attempts", "Actor", "Created", "Result"].map((h) => <th key={h} className="px-4 py-2 font-normal">{h}</th>)}</tr></thead>
            <tbody>
              {jobs.map((j) => (
                <Fragment key={j.id}>
                  <tr data-testid={`job-row-${j.id}`} onClick={() => setOpen(open === j.id ? null : j.id)} className="cursor-pointer border-t hover:bg-slate-800/40" style={{ borderColor: "var(--border-default)" }}>
                    <td className="px-4 py-2.5 font-mono text-cyan-300">{j.id.slice(0, 8)}</td>
                    <td className="px-4 py-2.5">{j.type}</td>
                    <td className="px-4 py-2.5 font-mono text-[10px] uppercase tracking-wider" style={{ color: STATUS_COLOR[j.status] }} data-testid={`job-status-${j.id}`}>{j.status}</td>
                    <td className="px-4 py-2.5 font-mono">{j.attempts}</td>
                    <td className="px-4 py-2.5 text-slate-400">{j.actor}</td>
                    <td className="px-4 py-2.5 font-mono text-slate-400">{fmtTime(j.created_at)}</td>
                    <td className="px-4 py-2.5 font-mono text-slate-300">{j.result ? `v${j.result.version} · ${j.result.overall_status} · ${j.result.candidates} cand.` : j.error || "—"}</td>
                  </tr>
                  {open === j.id && (
                    <tr><td colSpan={7} className="px-4 pb-3" data-testid={`job-log-${j.id}`}>
                      <div className="rounded p-3 font-mono text-[11px] leading-relaxed" style={{ background: "var(--bg-primary)", border: "1px solid var(--border-default)" }}>
                        {j.logs.map((l, i) => <div key={`${l.t}-${i}`} className={l.level === "error" ? "text-rose-300" : "text-slate-300"}><span className="text-slate-600">{l.t.slice(11, 19)}</span> {l.msg}</div>)}
                        {j.payload?.case_id && <button className="mt-2 text-cyan-300 hover:underline" onClick={() => nav(`/cases/${j.payload.case_id}`)} data-testid={`job-open-case-${j.id}`}>open case →</button>}
                      </div>
                    </td></tr>
                  )}
                </Fragment>
              ))}
              {jobs.length === 0 && <tr><td colSpan={7} className="px-4 py-8 text-center text-slate-500">No jobs.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="panel overflow-hidden" data-testid="alerts-list">
          <div className="border-b px-4 py-3 font-display font-semibold" style={{ borderColor: "var(--border-default)" }}>Alerts</div>
          <div className="space-y-2 p-3">
            {alerts.length === 0 && <p className="text-xs text-slate-500">No alerts raised.</p>}
            {alerts.map((a) => (
              <div key={a.id} className="rounded border p-3 text-xs" style={{ borderColor: a.acknowledged ? "var(--border-default)" : "rgba(255,42,109,0.5)" }} data-testid={`jobs-alert-${a.id}`}>
                <div className="flex justify-between"><button className="font-mono text-cyan-300 hover:underline" onClick={() => nav(`/cases/${a.case_id}`)}>{a.case_number}</button><span className="font-mono text-[10px] text-slate-500">{fmtTime(a.created_at)}</span></div>
                <p className="mt-1 text-slate-300">{a.message}</p>
                {a.icg && <p className="mt-1 font-mono text-[10px] text-emerald-300" data-testid={`jobs-alert-icg-${a.id}`}>⚓ routed → {a.icg.code} · {a.icg.district_hq}{a.icg.approximate ? " (approx.)" : ""}</p>}
                {a.notification && <p className="mt-1 font-mono text-[10px]" data-testid={`alert-notification-${a.id}`} style={{ color: a.notification.status === "sent" ? "#10B981" : a.notification.status === "not_configured" ? "#FFB703" : "#94A3B8" }}>email: {a.notification.status.replace("_", " ")} · {a.notification.sent}/{a.notification.recipients?.length || 0} recipients</p>}
                {a.acknowledged ? <p className="mt-1 font-mono text-[10px] text-emerald-300">ack by {a.acknowledged_by}</p> : hasRole(user, "supervisor") ?
                  <button onClick={() => ack(a.id)} data-testid={`jobs-alert-ack-${a.id}`} className="mt-2 inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider text-emerald-300 hover:underline"><Check size={12} /> Acknowledge</button>
                  : <p className="mt-1 font-mono text-[10px] text-slate-500">supervisor acknowledgement required</p>}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
