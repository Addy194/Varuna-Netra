import { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { ShieldAlert, Check, Waves, Ship, Clock, FileCheck } from "lucide-react";
import { api, apiError, fmtTime, pct, hasRole } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { StatusBadge, BandBadge } from "@/components/StatusBadge";
import { DetectorPrecision } from "@/components/dashboard/DetectorPrecision";
import { useLive } from "@/context/LiveFeed";

export default function Dashboard() {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const view = ["pending", "probable"].includes(params.get("view")) ? params.get("view") : "all";
  const origin = ["real", "imported", "demo", "all"].includes(params.get("origin")) ? params.get("origin") : "real";
  const [cases, setCases] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [stats, setStats] = useState(null);
  const [statsErr, setStatsErr] = useState(false);
  const [filter, setFilter] = useState("all");
  const nav = useNavigate();

  const load = useCallback(async () => {
    const [c, a] = await Promise.all([api.get(`/cases?origin=${origin}&limit=1000`), api.get("/alerts")]);
    setCases(c.data); setAlerts(a.data);
    try { const s = await api.get("/dashboard/summary"); setStats(s.data); setStatsErr(false); } catch { setStatsErr(true); }
  }, [origin]);
  useEffect(() => { load().catch((e) => toast.error(e.message)); }, [load]);
  const live = useLive();
  useEffect(() => {
    const a = live?.alerts?.[0];
    if (!a) return;
    setAlerts((prev) => (prev.some((x) => x.id === a.id) ? prev : [a, ...prev]));
  }, [live?.alerts]);
  useEffect(() => { if (live?.lastJob?.status === "succeeded") load().catch(() => {}); }, [live?.lastJob, load]);

  const ack = async (id) => {
    try { await api.post(`/alerts/${id}/ack`); toast.success("Alert acknowledged"); load(); window.dispatchEvent(new Event("varuna:refresh-counters")); }
    catch (e) { toast.error(apiError(e)); }
  };

  const all = cases || [];
  const base = view === "pending" ? all.filter((c) => c.status === "open" && c.review_state === "pending") : view === "probable" ? all.filter((c) => ["probable", "analyst_confirmed"].includes(c.attribution_status)) : all;
  const shown = filter === "all" ? base : base.filter((c) => c.attribution_status === filter);
  const kv = (v) => (statsErr ? "UNAVAILABLE" : stats ? v : "—");
  const kpis = [
    { label: "Live cases", value: kv(stats?.live_cases), icon: Waves, color: "#FF2A6D", to: "/?origin=real" },
    { label: "Probable / confirmed", value: kv(stats?.probable_confirmed), icon: Ship, color: "#FF6B00", to: "/?origin=real&view=probable" },
    { label: "Pending review", value: kv(stats?.pending_review), icon: Clock, color: "#FFB703", to: "/?origin=real&view=pending" },
    { label: "AIS fixes indexed", value: kv(stats?.ais_fixes_indexed), icon: FileCheck, color: "#00F0FF", to: "/ingest" },
  ];
  const ORIGIN_UI = { detector: ["REAL · SAR", "#10B981"], analyst: ["REAL · ANALYST", "#10B981"], imported: ["IMPORTED", "#FFB703"], demo: ["DEMO", "#94A3B8"], reference: ["REFERENCE", "#38BDF8"] };
  const heading = view === "pending" ? `Pending review · ${base.length} open cases awaiting analyst decision` : view === "probable" ? `Probable / confirmed · ${base.length}` : `Investigation cases · ${base.length}`;

  return (
    <div className="flex h-full overflow-hidden">
      <section className="flex-1 overflow-y-auto p-6">
        <div className="mb-6 fade-up">
          <p className="label-mono mb-1">Decision support · not a legal determination</p>
          <h1 className="font-display text-3xl font-extrabold tracking-tight sm:text-4xl">Spill Surveillance</h1>
        </div>
        <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
          {kpis.map((k, i) => (
            <button type="button" key={k.label} onClick={() => nav(k.to)} className="panel p-4 fade-up text-left hover:border-cyan-400/40 transition-colors" style={{ animationDelay: `${i * 60}ms` }} data-testid={`kpi-${k.label.toLowerCase().replace(/[^a-z]+/g, "-")}`}>
              <div className="flex items-center justify-between">
                <span className="label-mono">{k.label}</span>
                <k.icon size={14} color={k.color} />
              </div>
              <div className="mt-2 font-mono text-2xl font-semibold" style={{ color: k.color }}>{k.value}</div>
            </button>
          ))}
        </div>

        <div className="panel overflow-hidden fade-up" style={{ animationDelay: "240ms" }}>
          <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3" style={{ borderColor: "var(--border-default)" }}>
            <h2 className="font-display text-lg font-semibold mr-auto" data-testid="cases-heading">{heading}</h2>
            <span className="flex items-center gap-1" data-testid="origin-filter">
              {["real", "imported", "demo", "all"].map((o) => (
                <button key={o} data-testid={`origin-${o}`} onClick={() => setParams({ origin: o, ...(view !== "all" ? { view } : {}) })}
                  className={`rounded-full px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider transition-colors ${origin === o ? "bg-emerald-400/15 text-emerald-300 border border-emerald-400/40" : "text-slate-400 border border-slate-700 hover:text-slate-100"}`}>{o}{o === "imported" && stats ? ` ${stats.demo.imported}` : o === "demo" && stats ? ` ${stats.demo.cases}` : ""}</button>))}
            </span>
            {view !== "all" && <button data-testid="view-all-cases" onClick={() => setParams({ origin })} className="rounded-full px-3 py-1 font-mono text-[10px] uppercase tracking-wider border border-amber-400/40 text-amber-300">clear {view} filter</button>}
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
                  <td className="px-4 py-3 font-mono text-cyan-300">{c.case_number}<span className="ml-1.5 rounded px-1 py-0.5 text-[9px] uppercase tracking-wider" style={{ color: (ORIGIN_UI[c.origin] || ["UNTAGGED", "#94A3B8"])[1], border: `1px solid ${(ORIGIN_UI[c.origin] || ["", "#94A3B8"])[1]}55` }} data-testid={`case-origin-${c.case_number}`}>{(ORIGIN_UI[c.origin] || ["UNTAGGED"])[0]}</span></td>
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
              {cases === null && <tr><td colSpan={10} className="px-4 py-8 text-center text-slate-500" data-testid="cases-loading">—</td></tr>}
              {cases !== null && shown.length === 0 && <tr><td colSpan={10} className="px-4 py-8 text-center text-slate-500" data-testid="cases-empty">No {origin === "real" ? "real" : origin} cases match this filter.</td></tr>}
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
