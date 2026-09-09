import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ShieldAlert, Check, Waves, Ship, Clock, FileCheck } from "lucide-react";
import { api, apiError, fmtTime, pct, hasRole } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { StatusBadge, BandBadge } from "@/components/StatusBadge";
import { DetectorPrecision } from "@/components/dashboard/DetectorPrecision";
import { useLive } from "@/context/LiveFeed";

export default function Dashboard() {
  const { user } = useAuth();
  const [cases, setCases] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [stats, setStats] = useState(null);
  const [filter, setFilter] = useState("all");
  const nav = useNavigate();

  const load = async () => {
    const [c, a, s] = await Promise.all([api.get("/cases"), api.get("/alerts"), api.get("/stats")]);
    setCases(c.data); setAlerts(a.data); setStats(s.data);
  };
  useEffect(() => { load().catch((e) => toast.error(e.message)); }, []);
  const live = useLive();
  useEffect(() => {
    const a = live?.alerts?.[0];
    if (!a) return;
    setAlerts((prev) => (prev.some((x) => x.id === a.id) ? prev : [a, ...prev]));
  }, [live?.alerts]);
  useEffect(() => { if (live?.lastJob?.status === "succeeded") load().catch(() => {}); }, [live?.lastJob]);

  const ack = async (id) => {
    try { await api.post(`/alerts/${id}/ack`); toast.success("Alert acknowledged"); load(); }
    catch (e) { toast.error(apiError(e)); }
  };

  const shown = filter === "all" ? cases : cases.filter((c) => c.attribution_status === filter);
  const kpis = stats ? [
    { label: "Spill observations", value: stats.spill_observations, icon: Waves, color: "#FF2A6D" },
    { label: "Probable / confirmed", value: stats.by_attribution_status.probable + stats.by_attribution_status.analyst_confirmed, icon: Ship, color: "#FF6B00" },
    { label: "Pending review", value: stats.pending_review, icon: Clock, color: "#FFB703" },
    { label: "AIS fixes indexed", value: stats.ais_positions, icon: FileCheck, color: "#00F0FF" },
  ] : [];

  return (
    <div className="flex h-full overflow-hidden">
      <section className="flex-1 overflow-y-auto p-6">
        <div className="mb-6 fade-up">
          <p className="label-mono mb-1">Decision support · not a legal determination</p>
          <h1 className="font-display text-3xl font-extrabold tracking-tight sm:text-4xl">Spill Surveillance</h1>
        </div>
        <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
          {kpis.map((k, i) => (
            <div key={k.label} className="panel p-4 fade-up" style={{ animationDelay: `${i * 60}ms` }} data-testid={`kpi-${k.label.toLowerCase().replace(/[^a-z]+/g, "-")}`}>
              <div className="flex items-center justify-between">
                <span className="label-mono">{k.label}</span>
                <k.icon size={14} color={k.color} />
              </div>
              <div className="mt-2 font-mono text-2xl font-semibold" style={{ color: k.color }}>{k.value}</div>
            </div>
          ))}
        </div>

        <div className="panel overflow-hidden fade-up" style={{ animationDelay: "240ms" }}>
          <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3" style={{ borderColor: "var(--border-default)" }}>
            <h2 className="font-display text-lg font-semibold mr-auto">Investigation cases</h2>
            {["all", "probable", "possible", "indeterminate", "insufficient_evidence", "analyst_confirmed"].map((f) => (
              <button key={f} data-testid={`filter-${f}`} onClick={() => setFilter(f)}
                className={`rounded-full px-3 py-1 font-mono text-[10px] uppercase tracking-wider transition-colors ${filter === f ? "bg-cyan-400/15 text-cyan-300 border border-cyan-400/40" : "text-slate-400 border border-slate-700 hover:text-slate-100 hover:border-slate-500"}`}>
                {f.replace("_", " ")}
              </button>
            ))}
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="label-mono text-left">
                {["Case", "Acquired (UTC)", "Source", "Jurisdiction", "Det. conf", "Attribution", "Band", "Top score", "Candidates", "Review"].map((h) => (
                  <th key={h} className="px-4 py-2 font-normal">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {shown.map((c) => (
                <tr key={c.id} data-testid={`case-row-${c.case_number}`} onClick={() => nav(`/cases/${c.id}`)}
                  className="cursor-pointer border-t transition-colors hover:bg-slate-800/50" style={{ borderColor: "var(--border-default)" }}>
                  <td className="px-4 py-3 font-mono text-cyan-300">{c.case_number}</td>
                  <td className="px-4 py-3 font-mono text-xs text-slate-300">{fmtTime(c.acquisition_time)}</td>
                  <td className="px-4 py-3 text-xs text-slate-400">{c.source}</td>
                  <td className="px-4 py-3 font-mono text-[10px]" data-testid={`case-jurisdiction-${c.case_number}`} title={c.primary_jurisdiction?.authority}>{c.primary_jurisdiction ? <span className="text-cyan-300">{c.primary_jurisdiction.code}</span> : <span className="text-slate-500">unassigned</span>}</td>
                  <td className="px-4 py-3 font-mono text-xs">{pct(c.detection_confidence)}{c.quality_flags?.length > 0 && <span className="ml-1 text-amber-400" title={c.quality_flags.join(", ")}>⚑</span>}</td>
                  <td className="px-4 py-3"><StatusBadge status={c.attribution_status} testId={`case-status-${c.case_number}`} /></td>
                  <td className="px-4 py-3"><BandBadge band={c.confidence_band} /></td>
                  <td className="px-4 py-3 font-mono text-xs">{c.top_score != null ? c.top_score.toFixed(3) : "—"}{c.degraded && <span className="ml-1 text-purple-300" title="degraded: no drift inputs">◐</span>}</td>
                  <td className="px-4 py-3 font-mono text-xs">{c.candidate_count ?? "—"}</td>
                  <td className="px-4 py-3 font-mono text-[10px] uppercase tracking-wider text-slate-400">{c.review_state}</td>
                </tr>
              ))}
              {shown.length === 0 && <tr><td colSpan={10} className="px-4 py-8 text-center text-slate-500" data-testid="cases-empty">No cases match this filter.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <aside className="hidden w-80 shrink-0 flex-col border-l xl:flex" style={{ borderColor: "var(--border-default)", background: "var(--bg-secondary)" }}>
        <div className="flex items-center gap-2 border-b px-4 py-3" style={{ borderColor: "var(--border-default)" }}>
          <ShieldAlert size={14} color="#FF2A6D" />
          <h2 className="font-display font-semibold">Alerts</h2>
          <span className="ml-auto font-mono text-xs text-slate-400" data-testid="alerts-count">{alerts.filter((a) => !a.acknowledged).length} open</span>
        </div>
        <div className="border-b p-3" style={{ borderColor: "var(--border-default)" }}><DetectorPrecision /></div>
        <div className="flex-1 overflow-y-auto p-3 space-y-2">
          {alerts.length === 0 && <p className="p-3 text-xs text-slate-500" data-testid="alerts-empty">No alerts raised.</p>}
          {alerts.map((a) => (
            <div key={a.id} data-testid={`alert-${a.id}`} className="rounded-md border p-3 text-xs" style={{ borderColor: a.acknowledged ? "var(--border-default)" : "rgba(255,42,109,0.5)", background: a.acknowledged ? "transparent" : "rgba(255,42,109,0.06)" }}>
              <div className="flex items-center justify-between">
                <button className="font-mono text-cyan-300 hover:underline" onClick={() => nav(`/cases/${a.case_id}`)} data-testid={`alert-case-link-${a.id}`}>{a.case_number}</button>
                <span className="font-mono text-[10px] text-slate-500">{fmtTime(a.created_at)}</span>
              </div>
              <p className="mt-1.5 text-slate-300 leading-relaxed">{a.message}</p>
              {a.icg && <p className="mt-1 font-mono text-[10px] text-emerald-300" data-testid={`alert-icg-${a.id}`}>⚓ routed → {a.icg.code} · {a.icg.district_hq} · {a.icg.region_code}{a.icg.approximate ? " (approx.)" : ""}</p>}
              {!a.acknowledged && hasRole(user, "supervisor") && (
                <button onClick={() => ack(a.id)} data-testid={`alert-ack-${a.id}`} className="mt-2 inline-flex items-center gap-1 rounded px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-emerald-300 hover:bg-emerald-400/10">
                  <Check size={12} /> Acknowledge
                </button>
              )}
              {!a.acknowledged && !hasRole(user, "supervisor") && <p className="mt-2 font-mono text-[10px] text-slate-500" data-testid={`alert-ack-locked-${a.id}`}>supervisor acknowledgement required</p>}
            </div>
          ))}
        </div>
      </aside>
    </div>
  );
}
