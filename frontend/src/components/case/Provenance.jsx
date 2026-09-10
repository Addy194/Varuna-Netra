import { useEffect, useState } from "react";
import { Fingerprint } from "lucide-react";
import { api, fmtTime } from "@/lib/api";

const BADGE = { "REAL SENTINEL-1": "text-emerald-300 border-emerald-400/60", LIVE: "text-emerald-300 border-emerald-400/60", HISTORICAL: "text-cyan-300 border-cyan-400/60", EXPERIMENTAL: "text-amber-300 border-amber-400/60", MOCK: "text-rose-300 border-rose-400/60", DEMO: "text-rose-300 border-rose-400/60", NONE: "text-slate-400 border-slate-500", ANALYST: "text-cyan-300 border-cyan-400/60", EXTERNAL: "text-slate-300 border-slate-500" };
export const Badge = ({ v, testid }) => <span data-testid={testid} className={`rounded border px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase tracking-wider ${BADGE[v] || BADGE.NONE}`}>{v}</span>;
const age = (iso) => { if (!iso) return ""; const m = Math.round((Date.now() - new Date(iso)) / 60000); return m < 60 ? `${m} min ago` : m < 2880 ? `${Math.floor(m / 60)} h ${m % 60} m ago` : `${Math.round(m / 1440)} d ago`; };
const R = ({ k, v }) => <div className="flex justify-between gap-2 border-b border-dashed py-0.5" style={{ borderColor: "var(--border-default)" }}><span className="text-slate-500">{k}</span><span className="text-right text-slate-200 break-all">{v ?? "—"}</span></div>;

export const Provenance = ({ caseId }) => {
  const [p, setP] = useState(null);
  useEffect(() => { api.get(`/cases/${caseId}/provenance`).then((r) => setP(r.data)).catch(() => setP(false)); }, [caseId]);
  if (!p) return null;
  return (
    <div className="mx-4 my-3 rounded border p-3 font-mono text-[11px]" style={{ borderColor: "var(--border-highlight)", background: "rgba(0,240,255,0.03)" }} data-testid="provenance-panel">
      <div className="mb-2 flex items-center gap-2"><Fingerprint size={13} className="text-cyan-300" /><span className="font-display text-sm font-semibold">Evidence & data provenance</span><Badge v={p.data_mode} testid="provenance-data-mode" /></div>
      <div className="grid gap-x-6 gap-y-1 md:grid-cols-2">
        <div><p className="label-mono mb-1 flex items-center gap-2">Satellite <Badge v={p.satellite.badge} testid="provenance-sat-badge" /></p>
          <R k="provider" v={p.satellite.provider} /><R k="scene" v={p.satellite.scene_id} /><R k="acquired" v={p.satellite.acquisition_time ? `${fmtTime(p.satellite.acquisition_time)} · ${age(p.satellite.acquisition_time)}` : null} /><R k="platform / orbit" v={[p.satellite.platform, p.satellite.orbit_state].filter(Boolean).join(" · ") || null} /><R k="polarization" v={p.satellite.polarization} />
        </div>
        <div><p className="label-mono mb-1 flex items-center gap-2">Detection <Badge v={p.detection.badge} testid="provenance-det-badge" /></p>
          <R k="source" v={p.detection.source} /><R k="model / version" v={p.detection.model} /><R k="confidence" v={p.detection.confidence != null ? p.detection.confidence.toFixed(2) : null} />
        </div>
        <div><p className="label-mono mb-1 flex items-center gap-2">AIS <Badge v={p.ais.badge} testid="provenance-ais-badge" /></p>
          <R k="provider" v={p.ais.provider} /><R k="connection" v={p.ais.status.connected ? "connected" : `not connected — ${p.ais.status.reason}`} /><R k="observations used" v={p.ais.observations} />
        </div>
        <div><p className="label-mono mb-1">Analysis</p>
          <R k="algorithm" v={p.analysis.algorithm} /><R k="result version" v={p.analysis.version} /><R k="analysed at" v={p.analysis.analysed_at ? fmtTime(p.analysis.analysed_at) : null} /><R k="candidates" v={p.analysis.candidates} /><R k="input hash" v={p.analysis.input_hash?.slice(0, 16)} />
        </div>
      </div>
      {p.ais.badge === "NONE" && <p className="mt-2 text-amber-300/90" data-testid="provenance-ais-none">No AIS observations — satellite analysis is valid, vessel attribution is unavailable for this case.</p>}
    </div>
  );
};
