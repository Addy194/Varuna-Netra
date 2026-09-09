import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Radar, ScanSearch } from "lucide-react";
import { api, apiError, fmtTime } from "@/lib/api";

export const DarkVessels = ({ caseId, onScan }) => {
  const [scan, setScan] = useState(null);
  const [busy, setBusy] = useState(false);
  const [radius, setRadius] = useState(40);
  const load = useCallback(() => api.get(`/cases/${caseId}/dark-vessels`).then((r) => { setScan(r.data); onScan?.(r.data); }).catch(() => {}), [caseId, onScan]);
  useEffect(() => { load(); }, [load]);
  const run = async () => {
    setBusy(true);
    try { const { data } = await api.post(`/cases/${caseId}/dark-vessels/scan`, null, { params: { radius_km: radius } }); setScan(data); onScan?.(data); toast.success(`${data.dark_count} dark-vessel candidate(s) among ${data.targets.length} bright targets`); }
    catch (e) { toast.error(apiError(e)); } finally { setBusy(false); }
  };
  const dark = scan?.targets?.filter((t) => t.dark_candidate) || [];
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
      {scan?.status === "not_scanned" && <p className="mt-2 text-[11px] text-slate-500" data-testid="dark-not-scanned">Not scanned yet — needs a real Sentinel-1 quicklook on this case's scene.</p>}
      {scan?.targets && scan.status !== "not_scanned" && (
        <>
          <p className="mt-2 text-[11px] text-slate-400" data-testid="dark-summary">{scan.bright_targets_total} bright targets in scene · {scan.targets.length} within {scan.radius_km} km · <span className="text-rose-300">{scan.dark_count} without AIS ≤ {scan.dark_radius_km} km (±{scan.time_window_min} min, {scan.ais_fixes_checked} fixes checked)</span> · {fmtTime(scan.created_at)}</p>
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
