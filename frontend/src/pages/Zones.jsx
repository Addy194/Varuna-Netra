import { useCallback, useEffect, useState } from "react";
import { MapContainer, TileLayer, GeoJSON } from "react-leaflet";
import { toast } from "sonner";
import { Map as MapIcon, Plus, Trash2, RefreshCw, Globe } from "lucide-react";
import { api, apiError, hasRole, pollJob } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { ZoneRules } from "@/components/zones/ZoneRules";
import { IcgDistricts, icgColor } from "@/components/zones/IcgDistricts";

const icgStyle = (ft) => ({ color: icgColor(ft.properties.region_code), weight: 1, dashArray: "3,3", fillColor: icgColor(ft.properties.region_code), fillOpacity: 0.04 });
const icgTip = (ft, layer) => layer.bindTooltip(`ICG ${ft.properties.code} · ${ft.properties.name} (${ft.properties.approximate ? "approximate" : "official"})`, { sticky: true, className: "zone-tip" });

const TYPE_COLOR = { territorial: "#FF6B00", contiguous: "#FFB703", eez: "#38BDF8", port_state: "#10B981", custom: "#9D4EDD" };
const TYPE_LABEL = { territorial: "Territorial Sea (12 NM)", contiguous: "Contiguous Zone (24 NM)", eez: "EEZ (200 NM)", port_state: "Port state", custom: "Custom" };
const TYPE_DASH = { territorial: null, contiguous: "8,5", eez: "2,6", port_state: "1,4", custom: "4,4" };
const inputCls = "w-full rounded border bg-slate-900/60 px-2.5 py-1.5 font-mono text-xs text-slate-100 outline-none focus:border-cyan-400/60";
const bd = { borderColor: "var(--border-highlight)" };
const SAMPLE = JSON.stringify({ type: "Polygon", coordinates: [[[5.0, 52.0], [6.0, 52.0], [6.0, 52.6], [5.0, 52.6], [5.0, 52.0]]] });

export default function Zones() {
  const { user } = useAuth();
  const [zones, setZones] = useState([]);
  const [f, setF] = useState({ code: "", name: "", authority: "", country: "", zone_type: "eez", geometry: SAMPLE });
  const [busy, setBusy] = useState(false);
  const [iso, setIso] = useState("NLD, GBR, DEU, DNK, BEL, NOR");
  const [importLayers, setImportLayers] = useState(["eez"]);
  const [importing, setImporting] = useState(null);
  const admin = hasRole(user, "admin");
  const load = () => api.get("/jurisdictions").then((r) => setZones(r.data)).catch((e) => toast.error(apiError(e)));
  useEffect(() => { load(); }, []);
  const [icg, setIcg] = useState(null);
  const [showIcg, setShowIcg] = useState(true);
  const loadIcg = useCallback(() => api.get("/icg/districts/geojson").then((r) => setIcg(r.data)).catch(() => setIcg(null)), []);
  useEffect(() => { loadIcg(); }, [loadIcg]);
  const icgKey = icg ? icg.features.map((f) => f.properties.code + (f.properties.updated_by || "")).join("|") : "";

  const importOfficial = async () => {
    setImporting("queued…");
    try {
      const list = iso.split(/[,\s]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
      const { data: job } = await api.post("/jurisdictions/import/marine-regions", { iso3: list, replace_demo: true, layers: importLayers });
      const done = await pollJob(job.id, (j) => setImporting(`${j.status} · ${j.logs[j.logs.length - 1]?.msg || ""}`));
      if (done.status === "succeeded") toast.success(`Imported ${done.result.imported.length} official EEZ boundaries; ${done.result.cases_resolved} cases re-resolved${done.result.failed.length ? ` · failed: ${done.result.failed.map((f) => f.iso3).join(", ")}` : ""}`);
      else toast.error(`Import failed: ${done.error}`);
      load();
    } catch (e) { toast.error(apiError(e)); } finally { setImporting(null); }
  };

  const create = async () => {
    setBusy(true);
    try { await api.post("/jurisdictions", { ...f, geometry: JSON.parse(f.geometry), country: f.country || null }); toast.success(`Zone ${f.code} created`); setF({ ...f, code: "", name: "", authority: "" }); load(); }
    catch (e) { toast.error(apiError(e)); } finally { setBusy(false); }
  };
  const toggle = async (z) => { try { await api.put(`/jurisdictions/${z.id}`, { active: !z.active }); load(); } catch (e) { toast.error(apiError(e)); } };
  const remove = async (z) => { if (!window.confirm(`Delete zone ${z.code}?`)) return; try { await api.delete(`/jurisdictions/${z.id}`); toast.success("Zone deleted"); load(); } catch (e) { toast.error(apiError(e)); } };
  const resolveAll = async () => { try { const { data } = await api.post("/jurisdictions/resolve-all"); toast.success(`Re-resolved ${data.cases} cases: ${Object.entries(data.by_primary).map(([k, v]) => `${k} ${v}`).join(", ")}`); } catch (e) { toast.error(apiError(e)); } };

  const geojson = { type: "FeatureCollection", features: zones.filter((z) => z.active).map((z) => ({ type: "Feature", geometry: z.geometry, properties: z })) };

  return (
    <div className="flex h-full overflow-hidden" data-testid="zones-page">
      <div className="relative flex-1">
        <MapContainer center={[54.0, 4.0]} zoom={6} className="h-full w-full">
          <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" attribution="&copy; OpenStreetMap contributors" className="dark-tiles" updateWhenIdle updateWhenZooming={false} keepBuffer={0} />
          <GeoJSON key={zones.map((z) => z.id + z.active).join("|")} data={geojson}
            style={(ft) => ({ color: TYPE_COLOR[ft.properties.zone_type] || "#94A3B8", weight: ft.properties.zone_type === "territorial" ? 2.2 : 1.5, fillOpacity: 0.1, dashArray: TYPE_DASH[ft.properties.zone_type] ?? null })}
            onEachFeature={(ft, layer) => layer.bindTooltip(`${ft.properties.code} · ${ft.properties.authority}`, { sticky: true, className: "zone-tip" })} />
          {showIcg && icg && <GeoJSON key={`icg-${icgKey}`} data={icg} style={icgStyle} onEachFeature={icgTip} />}
        </MapContainer>
        <div className="absolute left-3 top-3 z-[1000] rounded px-3 py-2 text-[11px]" style={{ background: "rgba(10,14,23,0.85)", border: "1px solid var(--border-default)", backdropFilter: "blur(12px)" }}>
          {Object.entries(TYPE_COLOR).map(([k, c]) => <div key={k} className="flex items-center gap-2"><span className="h-2.5 w-4 border" style={{ borderColor: c, background: `${c}33` }} /> {k}</div>)}
          <label className="mt-1.5 flex items-center gap-2 border-t pt-1.5" style={{ borderColor: "var(--border-default)" }}><input data-testid="toggle-icg-layer" type="checkbox" checked={showIcg} onChange={(e) => setShowIcg(e.target.checked)} /> ICG districts (approx.)</label>
        </div>
      </div>
      <aside className="flex w-[460px] shrink-0 flex-col overflow-y-auto border-l p-4" style={{ borderColor: "var(--border-default)", background: "var(--bg-secondary)" }}>
        <p className="label-mono mb-1">Configurable boundaries · demo polygons are simplified, not official</p>
        <h1 className="font-display text-2xl font-bold tracking-tight">Jurisdiction zones</h1>
        <div className="mt-4 space-y-2" data-testid="zones-list">
          {zones.map((z) => (
            <div key={z.id} data-testid={`zone-row-${z.code}`} className="rounded border p-3 text-xs" style={{ borderColor: "var(--border-default)", opacity: z.active ? 1 : 0.5 }}>
              <div className="flex items-center gap-2">
                <span className="h-2.5 w-2.5 rounded-sm" style={{ background: TYPE_COLOR[z.zone_type] }} />
                <span className="font-mono text-cyan-300">{z.code}</span>
                <span className="font-mono text-[10px] uppercase tracking-wider text-slate-500">{TYPE_LABEL[z.zone_type] || z.zone_type}</span>
                {!z.active && <span className="font-mono text-[10px] text-slate-500">inactive</span>}
                {admin && (
                  <span className="ml-auto flex items-center gap-1">
                    <button data-testid={`zone-toggle-${z.code}`} onClick={() => toggle(z)} className="rounded px-1.5 py-0.5 font-mono text-[10px] text-slate-400 hover:text-amber-300">{z.active ? "disable" : "enable"}</button>
                    <button data-testid={`zone-delete-${z.code}`} onClick={() => remove(z)} className="rounded p-1 text-slate-400 hover:text-rose-400"><Trash2 size={12} /></button>
                  </span>
                )}
              </div>
              <div className="mt-1 text-slate-200">{z.name}</div>
              <div className="text-slate-400">{z.authority}{z.country ? ` · ${z.country}` : ""}</div>
              <div className="mt-1 font-mono text-[10px]" style={{ color: z.official ? "#10B981" : "#FFB703" }} data-testid={`zone-source-${z.code}`}>{z.official ? `official · MRGID ${z.mrgid}` : "demo polygon — not official"}</div>
            </div>
          ))}
        </div>
        <ZoneRules zones={zones} />
        <IcgDistricts onChanged={loadIcg} />
        {admin && (
          <div className="mt-5 rounded border p-4" style={{ borderColor: "rgba(0,240,255,0.35)", background: "rgba(0,240,255,0.04)" }} data-testid="zone-import-form">
            <div className="mb-2 flex items-center gap-2"><Globe size={14} color="#00F0FF" /><h2 className="font-display font-semibold">Import official EEZ boundaries</h2></div>
            <p className="mb-2 text-[11px] text-slate-400">Marine Regions Maritime Boundaries v12 (200 NM EEZ) via WFS, simplified for map performance. Replaces the demo boxes and re-resolves every case.</p>
            <input data-testid="zone-import-iso-input" className={inputCls} style={bd} value={iso} onChange={(e) => setIso(e.target.value)} placeholder="ISO3 codes, comma separated" />
            <div className="mt-2 flex flex-wrap items-center gap-3 font-mono text-[10px]" data-testid="zone-import-layers">
              {[["eez", "EEZ 200 NM"], ["eez_24nm", "Contiguous 24 NM"], ["eez_12nm", "Territorial 12 NM"]].map(([k, l]) => (
                <label key={k} className="flex items-center gap-1 text-slate-300"><input type="checkbox" data-testid={`zone-import-layer-${k}`} checked={importLayers.includes(k)} onChange={(e) => setImportLayers(e.target.checked ? [...importLayers, k] : importLayers.filter((x) => x !== k))} /> {l}</label>))}
              <button data-testid="zone-import-india-preset" onClick={() => { setIso("IND"); setImportLayers(["eez", "eez_24nm", "eez_12nm"]); }} className="rounded border px-2 py-0.5 uppercase tracking-wider text-amber-300" style={{ borderColor: "rgba(255,183,3,0.5)" }}>India · all 3 zones</button>
            </div>
            <div className="mt-2 flex items-center gap-2">
              <button data-testid="btn-import-eez" disabled={!!importing} onClick={importOfficial} className="inline-flex items-center gap-1.5 rounded bg-cyan-400 px-3 py-1.5 font-mono text-[11px] font-semibold uppercase tracking-wider text-slate-950 hover:bg-cyan-300 disabled:opacity-50"><Globe size={12} /> {importing ? "Importing…" : "Import from Marine Regions"}</button>
              {importing && <span className="font-mono text-[10px] text-cyan-300 truncate" data-testid="zone-import-status">{importing}</span>}
            </div>
          </div>
        )}
        {admin && (
          <div className="mt-5 rounded border p-4" style={{ borderColor: "var(--border-default)" }} data-testid="zone-create-form">
            <div className="mb-3 flex items-center gap-2"><MapIcon size={14} color="#00F0FF" /><h2 className="font-display font-semibold">Add zone</h2></div>
            <div className="grid grid-cols-2 gap-2">
              <input data-testid="zone-code-input" placeholder="code e.g. NOR-EEZ" className={inputCls} style={bd} value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} />
              <select data-testid="zone-type-select" className={inputCls} style={bd} value={f.zone_type} onChange={(e) => setF({ ...f, zone_type: e.target.value })}>{Object.keys(TYPE_COLOR).map((t) => <option key={t} value={t}>{t}</option>)}</select>
              <input data-testid="zone-name-input" placeholder="name" className={`${inputCls} col-span-2`} style={bd} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
              <input data-testid="zone-authority-input" placeholder="responsible authority" className={inputCls} style={bd} value={f.authority} onChange={(e) => setF({ ...f, authority: e.target.value })} />
              <input data-testid="zone-country-input" placeholder="country ISO (optional)" className={inputCls} style={bd} value={f.country} onChange={(e) => setF({ ...f, country: e.target.value })} />
              <textarea data-testid="zone-geometry-input" rows={4} className={`${inputCls} col-span-2`} style={bd} value={f.geometry} onChange={(e) => setF({ ...f, geometry: e.target.value })} placeholder="GeoJSON Polygon / MultiPolygon ([lon, lat])" />
            </div>
            <div className="mt-3 flex items-center gap-2">
              <button data-testid="btn-create-zone" disabled={busy} onClick={create} className="inline-flex items-center gap-1.5 rounded bg-cyan-400 px-3 py-1.5 font-mono text-[11px] font-semibold uppercase tracking-wider text-slate-950 hover:bg-cyan-300 disabled:opacity-50"><Plus size={12} /> Create zone</button>
              <button data-testid="btn-resolve-all" onClick={resolveAll} className="inline-flex items-center gap-1.5 rounded border px-3 py-1.5 font-mono text-[11px] uppercase tracking-wider text-slate-300 hover:text-white" style={bd}><RefreshCw size={12} /> Re-resolve all cases</button>
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}
