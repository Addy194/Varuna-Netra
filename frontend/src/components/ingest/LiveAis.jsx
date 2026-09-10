import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Radio, Wifi, WifiOff } from "lucide-react";
import { api, apiError, fmtTime, hasRole } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

const bd = { borderColor: "var(--border-highlight)" };
const REGION_LABELS = { west_coast: "West Coast", east_coast: "East Coast", south_india: "South India", andaman_nicobar: "Andaman & Nicobar", default: "All India (default)" };

export const LiveAis = ({ onChanged }) => {
  const { user } = useAuth();
  const [s, setS] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = () => api.get("/ais/status").then((r) => setS(r.data)).catch((e) => toast.error(apiError(e)));
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, []);
  const setRegion = async (k) => {
    setBusy(true);
    try { const { data } = await api.post(`/ais/coverage/region/${k}`); toast.success(`AIS coverage → ${data.name}`); await load(); onChanged?.(); }
    catch (e) { toast.error(apiError(e)); } finally { setBusy(false); }
  };
  const test = async () => {
    setBusy(true);
    try { const { data } = await api.post("/ais/test-connection"); (data.message_received ? toast.success : toast.warning)(`key ${data.configured} · websocket ${data.websocket} · subscription ${data.subscription} · message ${data.message_received}${data.error ? ` · ${data.error}` : ""}`); }
    catch (e) { toast.error(apiError(e)); } finally { setBusy(false); }
  };
  if (!s) return null;
  const tone = s.state === "LIVE" ? "#10B981" : s.state === "CONNECTED" || s.state === "CONNECTING" || s.state === "RECONNECTING" ? "#FFB703" : "#FF2A6D";
  const label = s.state === "LIVE" ? `LIVE AIS · ${s.messages_per_min} msg/min` : s.state === "CONNECTED" ? "connected · awaiting messages" : s.state === "NOT_CONFIGURED" ? "NOT CONFIGURED — API key not configured" : s.state === "CONNECTING" || s.state === "RECONNECTING" ? s.state.toLowerCase() : `AIS OFFLINE — ${s.reason}`;
  return (
    <div className="panel p-5 fade-up" data-testid="live-ais-panel">
      <div className="mb-2 flex items-center gap-2"><Radio size={16} color="#00F0FF" /><h2 className="font-display text-lg font-semibold">Live AIS feed (AISStream)</h2>
        <span data-testid="live-ais-badge" className="ml-auto inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-wider" style={{ color: tone, border: `1px solid ${tone}66` }}>{s.state === "LIVE" ? <Wifi size={10} /> : <WifiOff size={10} />} {label}</span></div>
      <p className="mb-3 text-xs text-slate-400">Genuine AISStream WebSocket only — no simulated vessels. Key is read server-side from <code>AISSTREAM_API_KEY</code>{s.configured ? " (configured)" : " (NOT configured — add it to the backend environment)"}.</p>
      <div className="grid grid-cols-4 gap-3 font-mono text-[11px]" data-testid="live-ais-stats">
        {[["messages", s.messages_received], ["positions stored", s.positions_stored], ["active vessels", s.vessels_active], ["reconnects", s.reconnects]].map(([l, v]) => <div key={l} className="rounded border p-2" style={{ borderColor: "var(--border-default)" }}><div className="label-mono">{l}</div><div className="text-sm text-slate-100">{v}</div></div>)}
      </div>
      <p className="mt-2 font-mono text-[10px] text-slate-500" data-testid="live-ais-coverage">coverage {s.coverage_mode.toUpperCase()} · {s.coverage_name} · [S,W,N,E] {s.coverage_bbox.map((b) => b.map((x) => x.toFixed(1)).join(",")).join(" | ")} · last message {fmtTime(s.last_message_at)}{s.error && <span className="text-rose-300"> · {s.error}</span>}</p>
      {hasRole(user, "supervisor") && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <span className="label-mono mr-1">Monitor region</span>
          {Object.entries(REGION_LABELS).map(([k, l]) => <button key={k} data-testid={`live-ais-region-${k}`} disabled={busy} onClick={() => setRegion(k)} className="rounded border px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-slate-300 hover:text-white disabled:opacity-50" style={bd}>{l}</button>)}
          {hasRole(user, "admin") && <button data-testid="btn-test-ais" disabled={busy} onClick={test} className="ml-auto rounded bg-cyan-400 px-3 py-1 font-mono text-[10px] font-semibold uppercase tracking-wider text-slate-950 disabled:opacity-50">Test connection</button>}
        </div>
      )}
    </div>
  );
};
