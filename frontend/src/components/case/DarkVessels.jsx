import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Radar, ScanSearch, Satellite } from "lucide-react";
import { api, apiError, fmtTime } from "@/lib/api";

const STATE_UI = {
  SAR_READY: { color: "#10B981", label: "SAR READY", hint: "Real Sentinel-1 SAR attached · dark-vessel scan available" },
  QUICKLOOK_GENERATING: { color: "#FFB703", label: "QUICKLOOK GENERATING", hint: "SAR available · preview still being generated" },
  SAR_UNAVAILABLE: { color: "#FF2A6D", label: "SAR UNAVAILABLE", hint: "No usable Sentinel-1 analysis asset attached" },
};

const SceneStatus = ({ s, caseId, onAttached }) => {
  const [busy, setBusy] = useState(false);
  if (!s) return null;
  const ui = STATE_UI[s.state] || STATE_UI.SAR_UNAVAILABLE;
  const attach = async () => {
    setBusy(true);
    try { const { data } = await api.post(`/cases/${caseId}/attach-scene`, {}); toast.success(`Attached ${data.scene.provider_scene_id} · ${data.scene_status.state}`); onAttached?.(); }
    catch (e) { toast.error(apiError(e)); } finally { setBusy(false); }
  };
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 font-mono text-[10px]" data-testid="scene-sar-status">
      <span className="rounded px-1.5 py-0.5 uppercase tracking-wider" style={{ color: ui.color, border: `1px solid ${ui.color}66` }} data-testid="scene-sar-state">{ui.label}</span>
      <span className="text-slate-400">{s.reason || ui.hint}{s.provider_scene_id ? ` · ${s.provider_scene_id}` : ""}{s.analysis_asset ? ` · analysis asset ${s.analysis_asset}` : ""}{s.quicklook_kind ? ` · quicklook ${s.quicklook_kind}` : ""}</span>
      {!s.scene_id && <button data-testid="btn-attach-scene" disabled={busy} onClick={attach} className="inline-flex items-center gap-1 rounded border px-2 py-0.5 uppercase tracking-wider text-cyan-300 hover:bg-cyan-400/10 disabled:opacity-50" style={{ borderColor: "rgba(0,240,255,0.4)" }}><Satellite size={10} /> {busy ? "Searching STAC…" : "Find & attach Sentinel-1 scene"}</button>}
    </div>
  );
};

export const DarkVessels = ({ caseId, onScan, sceneStatus, onAttached }) => {
  const [scan, setScan] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [radius, setRadius] = useState(40);
  const load = useCallback(() => api.get(`/cases/${caseId}/dark-vessels`).then((r) => { setScan(r.data); onScan?.(r.data); }).catch((e) => setError(apiError(e))), [caseId, onScan]);
  useEffect(() => { load(); }, [load]);
  const run = async () => {
    setBusy(true); setError(null);
    try { const { data } = await api.post(`/cases/${caseId}/dark-vessels/scan`, null, { params: { radius_km: radius } }); setScan(data); onScan?.(data); toast.success(`${data.dark_count} dark-vessel candidate(s) among ${data.targets.length} bright targets`); onAttached?.(); }
    catch (e) { setError(apiError(e)); toast.error(apiError(e)); } finally { setBusy(false); }
  };
  const dark = scan?.targets?.filter((t) => t.dark_candidate) || [];
  const noAis = scan?.ais_fixes_checked === 0;
  return (
    <div className="mx-4 my-3 rounded border p-3" style={{ borderColor: "rgba(255,42,109,0.45)", background: "rgba(255,42,109,0.04)" }} data-testid="dark-vessels-panel">
      <div className="flex flex-wrap items-center gap-2">
        <Radar size={13} color="#FF2A6D" /><span className="font-display text-sm font-semibold">Dark vessel scan</span>
        <span className="rounded px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-amber-300" style={{ border: "1px solid currentColor" }}>experimental · CFAR</span>
        <span className="ml-auto flex items-center gap-2 font-mono text-[11px]">
          <label className="text-slate-400">radius <input data-testid="dark-scan-radius" type="number" min={5} max={150} value={radius} onChange={(e) => setRadius(+e.target.value)} className="w-14 rounded border bg-slate-900/60 px-1 py-0.5 text-slate-100" style={{ borderColor: "var(--border-highlight)" }} /> km</label>
          <button data-testid="btn-dark-scan" disabled={busy} onClick={run} className="inline-flex items-center gap-1 rounded bg-rose-500 px-2.5 py-1 font-semibold uppercase tracking-wider text-slate-950 disabled:opacity-50"><ScanSearch size={11} /> {busy ? "Scanning…" : "Scan SAR for ships"}</button>
        </span>
      </div>
      <SceneStatus s={sceneStatus} caseId={caseId} onAttached={onAttached} />
      {error && <p className="mt-2 text-[11px] text-rose-300" data-testid="dark-scan-error">{error}</p>}
      {scan?.status === "not_scanned" && !error && <p className="mt-2 text-[11px] text-slate-500" data-testid="dark-not-scanned">Not scanned yet. {sceneStatus?.state === "SAR_READY" ? "Real SAR asset ready — run the scan." : "Attach a Sentinel-1 scene with a SAR asset first."}</p>}
      {scan?.targets && scan.status !== "not_scanned" && (
        <>
          <p className="mt-2 text-[11px] text-slate-400" data-testid="dark-summary">{scan.bright_targets_total} bright targets in {scan.analysis_input?.kind === "sar_aoi_window" ? `real SAR AOI window (${scan.analysis_input.asset})` : "scene quicklook"} · {scan.targets.length} within {scan.radius_km} km · <span className="text-rose-300">{scan.dark_count} without AIS ≤ {scan.dark_radius_km} km (±{scan.time_window_min} min, {scan.ais_fixes_checked} fixes checked)</span> · {fmtTime(scan.created_at)}</p>
          {noAis && <p className="mt-1 text-[10px] text-amber-300" data-testid="dark-no-ais-warning">No AIS fixes exist for this time window — every bright target is unmatched by construction. Connect live AIS or ingest historical AIS before treating any target as "dark".</p>}
          <p className="mt-1 text-[10px] text-amber-300/80">{scan.disclaimer}</p>
          {dark.length > 0 && (
            <table className="mt-2 w-full text-[11px]" data-testid="dark-vessels-table">
              <thead><tr className="label-mono text-left">{["#", "Position", "SNR", "≈ length", "Dist. to slick", "Nearest AIS", "Escape cue"].map((h) => <th key={h} className="px-2 py-1 font-normal">{h}</th>)}</tr></thead>
              <tbody>{dark.map((t, i) => (
                <tr key={t.id} data-testid={`dark-vessel-${t.id}`} className="border-t" style={{ borderColor: "var(--border-default)" }}>
                  <td className="px-2 py-1 font-mono text-rose-300">D{i + 1}</td>
                  <td className="px-2 py-1 font-mono text-slate-200">{t.lat.toFixed(4)}, {t.lon.toFixed(4)}</td>
                  <td className="px-2 py-1 font-mono">{t.snr}σ</td>
                  <td className="px-2 py-1 font-mono">{t.est_length_m} m</td>
                  <td className="px-2 py-1 font-mono">{t.distance_to_spill_km} km</td>
                  <td className="px-2 py-1 font-mono text-slate-400">{t.nearest_ais_km != null ? `${t.nearest_ais_km} km` : "none in window"}</td>
                  <td className="px-2 py-1 font-mono text-slate-400">{t.escape_heading_deg}° @ {t.assumed_speed_kn} kn</td>
                </tr>))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  );
};
