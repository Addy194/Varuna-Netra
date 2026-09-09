import { useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronRight, AlertTriangle, History, Eye } from "lucide-react";
import { StatusBadge, ScoreBar } from "@/components/StatusBadge";
import { rankColor } from "@/components/case/CaseMap";
import { fmtTime } from "@/lib/api";

const FACTORS = ["spatial", "temporal", "continuity", "heading", "drift", "reliability"];

export const CandidatesTable = ({ candidates, selected, onSelect }) => {
  const [open, setOpen] = useState(null);
  if (!candidates?.length) {
    return <p className="p-6 text-sm text-slate-500" data-testid="candidates-empty">No AIS candidates within the search corridor and time window.</p>;
  }
  return (
    <div className="divide-y" style={{ borderColor: "var(--border-default)" }}>
      {candidates.map((c) => {
        const isOpen = open === c.mmsi;
        const sel = selected === c.mmsi;
        return (
          <div key={c.mmsi} data-testid={`candidate-vessel-row-${c.mmsi}`} className={`transition-colors ${sel ? "bg-slate-800/60" : "hover:bg-slate-800/30"}`} style={{ borderColor: "var(--border-default)" }}>
            <div className="flex cursor-pointer items-center gap-3 px-4 py-3" onClick={() => { onSelect?.(c.mmsi); setOpen(isOpen ? null : c.mmsi); }}>
              <span className="grid h-7 w-7 shrink-0 place-items-center rounded font-mono text-xs font-bold" style={{ background: `${rankColor(c.rank)}22`, color: rankColor(c.rank), border: `1px solid ${rankColor(c.rank)}66` }}>{c.rank}</span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                  <span className="font-display font-semibold">{c.vessel_name || "UNKNOWN"}</span>
                  <span className="font-mono text-[10px] text-slate-400">MMSI {c.mmsi}{c.imo ? ` · IMO ${c.imo}` : ""}{c.vessel_type ? ` · ${c.vessel_type}` : ""}</span>
                  <Link to={`/vessels/${c.mmsi}`} data-testid={`vessel-history-link-${c.mmsi}`} onClick={(e) => e.stopPropagation()} title="Vessel history" className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[10px] text-cyan-300 hover:bg-cyan-400/10"><History size={11} /> history</Link>
                  {c.ais_flags?.length > 0 && <AlertTriangle size={12} color="#FFB703" title={c.ais_flags.join(", ")} />}
                  {c.watchlist && <span data-testid={`watchlist-badge-${c.mmsi}`} title={c.watchlist.reason} className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-mono text-[9px] uppercase tracking-wider" style={{ color: "#FF2A6D", background: "rgba(255,42,109,0.12)", border: "1px solid rgba(255,42,109,0.5)" }}><Eye size={10} /> watchlist</span>}
                </div>
                <div className="mt-1.5 flex items-center gap-3">
                  <ScoreBar value={c.score} color={rankColor(c.rank)} testId={`candidate-score-bar-${c.mmsi}`} />
                  <span className="font-mono text-xs w-12 text-right" data-testid={`candidate-score-${c.mmsi}`}>{c.score.toFixed(3)}</span>
                </div>
              </div>
              <StatusBadge status={c.status} testId={`candidate-status-${c.mmsi}`} />
              {c.zone && <span data-testid={`candidate-zone-${c.mmsi}`} title={`${c.zone.name} · ${c.zone.authority}`} className="ml-1 rounded px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider" style={{ color: "#38BDF8", border: "1px solid rgba(56,189,248,0.5)" }}>{c.zone.code} · {c.zone.zone_label}</span>}
              <button className="text-slate-400" data-testid={`score-breakdown-toggle-${c.mmsi}`}>{isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</button>
            </div>
            {isOpen && (
              <div className="px-4 pb-4 fade-up" data-testid={`score-breakdown-${c.mmsi}`}>
                <div className="grid gap-2 sm:grid-cols-2">
                  {FACTORS.map((k) => {
                    const f = c.factors[k];
                    return (
                      <div key={k} className="rounded border p-2.5" style={{ borderColor: "var(--border-default)", background: "var(--bg-secondary)" }} data-testid={`factor-${k}-${c.mmsi}`}>
                        <div className="flex items-center justify-between">
                          <span className="label-mono">{k} <span className="text-slate-600">w={f.weight}</span></span>
                          <span className="font-mono text-xs">{f.score.toFixed(2)} <span className="text-slate-500">→ +{f.contribution.toFixed(3)}</span></span>
                        </div>
                        <ScoreBar value={f.score} color="#00F0FF" />
                        <p className="mt-1.5 text-[11px] leading-snug text-slate-400">{f.detail}</p>
                      </div>
                    );
                  })}
                </div>
                <div className="mt-3 grid gap-1 font-mono text-[11px] text-slate-400 sm:grid-cols-2">
                  <span>Closest fix: {fmtTime(c.evidence.closest_fix.timestamp)} · {c.evidence.closest_fix.lat.toFixed(4)}, {c.evidence.closest_fix.lon.toFixed(4)}</span>
                  <span>Distance {c.evidence.distance_km} km · gap {c.evidence.time_gap_hours}h · {c.evidence.fix_count} fixes · max AIS gap {c.evidence.max_gap_hours}h</span>
                </div>
                {c.notes?.length > 0 && (
                  <ul className="mt-2 space-y-0.5 text-[11px] text-amber-300/90" data-testid={`candidate-notes-${c.mmsi}`}>
                    {c.notes.map((n) => <li key={n}>▸ {n}</li>)}
                  </ul>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};
