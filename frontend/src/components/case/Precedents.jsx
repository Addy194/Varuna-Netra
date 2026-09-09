import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { BookOpen } from "lucide-react";
import { api, fmtTime } from "@/lib/api";

export const Precedents = ({ caseId }) => {
  const [d, setD] = useState(null);
  useEffect(() => { api.get(`/cases/${caseId}/precedents`).then((r) => setD(r.data)).catch(() => {}); }, [caseId]);
  if (!d) return null;
  return (
    <div className="rounded border p-3" style={{ borderColor: "rgba(56,189,248,0.4)", background: "rgba(56,189,248,0.04)" }} data-testid="precedents-drawer">
      <div className="mb-1 flex items-center gap-2"><BookOpen size={13} color="#38BDF8" /><span className="font-display text-sm font-semibold">Precedents</span><span className="font-mono text-[10px] text-slate-500">similarity = 0.5·distance + 0.3·volume + 0.2·oil type · est. {d.estimated_volume_tonnes} t</span><Link to="/archive" className="ml-auto font-mono text-[10px] text-cyan-300 hover:underline" data-testid="precedents-archive-link">open archive →</Link></div>
      <div className="grid grid-cols-3 gap-2">
        {d.precedents.map((p) => (
          <div key={p.id} data-testid={`precedent-${p.id}`} className="rounded border p-2 text-xs" style={{ borderColor: "var(--border-default)" }}>
            <div className="flex items-center gap-2"><span className="font-display font-semibold text-slate-100">{p.name}</span><span className="ml-auto font-mono text-[10px] text-cyan-300">{(p.similarity * 100).toFixed(0)}%</span></div>
            <div className="font-mono text-[10px] text-slate-500">{fmtTime(p.date).slice(0, 10)} · {p.country} · {p.distance_km} km · {p.volume_tonnes?.toLocaleString()} t {p.oil_type}</div>
            <div className="mt-1 text-[11px] text-slate-400"><span className="text-slate-300">Remediation:</span> {p.remediation.slice(0, 3).join("; ")}</div>
            <div className="mt-1 text-[11px] italic text-slate-400">{p.lessons}</div>
          </div>
        ))}
      </div>
    </div>
  );
};
