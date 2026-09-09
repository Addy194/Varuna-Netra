import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Radio, Wifi, WifiOff } from "lucide-react";
import { api, apiError, fmtTime, hasRole } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

const inputCls = "w-full rounded border bg-slate-900/60 px-2.5 py-1.5 font-mono text-xs text-slate-100 outline-none focus:border-cyan-400/60";
const bd = { borderColor: "var(--border-highlight)" };
const REGIONS = { "North Sea": [[[50, -5], [62, 12]]], "Global": [[[-90, -180], [90, 180]]], "Gulf of Mexico": [[[18, -98], [31, -80]]], "Mediterranean": [[[30, -6], [46, 37]]], "SE Asia": [[[-10, 95], [25, 125]]] };

export const LiveAis = ({ onChanged }) => {
  const { user } = useAuth();
  const [s, setS] = useState(null);
  const [key, setKey] = useState("");
  const [region, setRegion] = useState("North Sea");
  const [busy, setBusy] = useState(false);
  const load = () => api.get("/ais/live/status").then((r) => setS(r.data)).catch((e) => toast.error(apiError(e)));
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, []);

  const save = async (body) => {
    setBusy(true);
    try { const { data } = await api.put("/ais/live/settings", body); setS(data); setKey(""); toast.success("Live AIS settings saved"); onChanged?.(); }
    catch (e) { toast.error(apiError(e)); } finally { setBusy(false); }
  };
  if (!s) return null;
  const admin = hasRole(user, "admin");
  return (
    <div className="panel p-5 fade-up" data-testid="live-ais-panel">
      <div className="mb-2 flex items-center gap-2"><Radio size={16} color="#00F0FF" /><h2 className="font-display text-lg font-semibold">Live global AIS feed</h2>
        <span data-testid="live-ais-badge" className="ml-auto inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-wider" style={{ color: s.connected ? "#10B981" : s.configured ? "#FFB703" : "#94A3B8", border: `1px solid ${s.connected ? "#10B981" : s.configured ? "#FFB703" : "#94A3B8"}66` }}>{s.connected ? <Wifi size={10} /> : <WifiOff size={10} />} {s.connected ? "streaming" : s.mode === "demo" ? "demo replay" : !s.configured ? "not configured" : s.enabled ? "connecting…" : "paused"}</span></div>
      <p className="mb-3 text-xs text-slate-400">{s.mode === "demo" ? "Deterministic demo AIS replay — no external key required; switch to live mode by supplying an AISStream key." : `${s.provider} — terrestrial AIS position reports streamed into the AIS store for any region. Coverage depends on shore-station density; ocean gaps are expected.`}</p>
      <div className="grid grid-cols-4 gap-3 font-mono text-[11px]" data-testid="live-ais-stats">
        {[["messages", s.messages], ["positions", s.positions], ["inserted", s.inserted], ["vessels", s.vessels]].map(([l, v]) => <div key={l} className="rounded border p-2" style={{ borderColor: "var(--border-default)" }}><div className="label-mono">{l}</div><div className="text-sm text-slate-100">{v}</div></div>)}
      </div>
      <p className="mt-2 font-mono text-[10px] text-slate-500">last message {fmtTime(s.last_message_at)} · boxes {JSON.stringify(s.bboxes)}{s.error && <span className="text-rose-300"> · {s.error}</span>}</p>
      {admin && (
        <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_180px_auto_auto]">
          <input data-testid="live-ais-key-input" type="password" className={inputCls} style={bd} value={key} onChange={(e) => setKey(e.target.value)} placeholder={s.api_key ? `key ${s.api_key} (${s.source}) — paste to replace` : "aisstream.io API key"} autoComplete="off" />
          <select data-testid="live-ais-region-select" className={inputCls} style={bd} value={region} onChange={(e) => setRegion(e.target.value)}>{Object.keys(REGIONS).map((r) => <option key={r}>{r}</option>)}</select>
          <button data-testid="btn-save-live-ais" disabled={busy} onClick={() => save({ ...(key ? { api_key: key, mode: "live" } : { mode: "demo" }), bboxes: REGIONS[region], enabled: true })} className="rounded bg-cyan-400 px-3 py-1.5 font-mono text-[11px] font-semibold uppercase tracking-wider text-slate-950 hover:bg-cyan-300 disabled:opacity-50">Save & start</button>
          <button data-testid="btn-toggle-live-ais" disabled={busy} onClick={() => save({ enabled: !s.enabled })} className="rounded border px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider text-slate-300 hover:text-white disabled:opacity-50" style={bd}>{s.enabled ? "Pause" : "Resume"}</button>
        </div>
      )}
    </div>
  );
};
