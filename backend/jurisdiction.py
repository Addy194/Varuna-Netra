from datetime import datetime, timezone

from shapely.geometry import shape

from db import db, audit
from geo import validate_polygon
from models import new_id

ZONE_TYPES = ["eez", "territorial", "port_state", "custom"]
TYPE_PRIORITY = {"port_state": 0, "territorial": 1, "contiguous": 2, "eez": 3, "custom": 4}
ZONE_LABELS = {"territorial": "Territorial Sea (12 NM)", "contiguous": "Contiguous Zone (24 NM)", "eez": "Exclusive Economic Zone (200 NM)", "port_state": "Port state waters", "custom": "Custom zone"}


async def resolve_point_zones(lat: float, lon: float):
    """Zones containing a point (most specific first) — used to tag vessel positions."""
    zones = await db.jurisdictions.find({"active": True, "geometry": {"$geoIntersects": {"$geometry": {"type": "Point", "coordinates": [lon, lat]}}}}, {"_id": 0, "geometry": 0}).to_list(50)
    zones.sort(key=lambda z: TYPE_PRIORITY.get(z["zone_type"], 9))
    return [{"code": z["code"], "name": z["name"], "zone_type": z["zone_type"], "zone_label": ZONE_LABELS.get(z["zone_type"], z["zone_type"]), "country": z.get("country"), "authority": z["authority"]} for z in zones]

DEMO_ZONES = [
    {"code": "GBR-EEZ", "name": "United Kingdom EEZ (demo, simplified)", "authority": "UK Maritime & Coastguard Agency", "country": "GB", "zone_type": "eez",
     "geometry": {"type": "Polygon", "coordinates": [[[-2.0, 51.0], [2.0, 51.0], [2.5, 52.5], [3.0, 53.5], [3.2, 55.0], [2.0, 56.5], [-2.0, 56.5], [-2.0, 51.0]]]}},
    {"code": "BEL-EEZ", "name": "Belgium EEZ (demo, simplified)", "authority": "Belgian FPS Mobility / MUMM", "country": "BE", "zone_type": "eez",
     "geometry": {"type": "Polygon", "coordinates": [[[2.5, 51.1], [3.4, 51.4], [3.2, 51.9], [2.4, 51.9], [2.5, 51.1]]]}},
    {"code": "NLD-EEZ", "name": "Netherlands EEZ (demo, simplified)", "authority": "Rijkswaterstaat / Netherlands Coastguard", "country": "NL", "zone_type": "eez",
     "geometry": {"type": "Polygon", "coordinates": [[[3.4, 51.4], [4.8, 52.9], [6.6, 53.5], [6.4, 55.0], [3.2, 55.0], [3.0, 53.5], [2.5, 52.5], [3.2, 51.9], [3.4, 51.4]]]}},
    {"code": "DEU-EEZ", "name": "Germany EEZ (demo, simplified)", "authority": "Havariekommando (CCME)", "country": "DE", "zone_type": "eez",
     "geometry": {"type": "Polygon", "coordinates": [[[6.6, 53.5], [9.0, 53.9], [8.5, 55.1], [6.4, 55.0], [6.6, 53.5]]]}},
    {"code": "DNK-EEZ", "name": "Denmark EEZ (demo, simplified)", "authority": "Danish Defence – Maritime Assistance Service", "country": "DK", "zone_type": "eez",
     "geometry": {"type": "Polygon", "coordinates": [[[3.2, 55.0], [6.4, 55.0], [8.5, 55.1], [8.2, 57.0], [4.5, 57.0], [3.2, 55.0]]]}},
    {"code": "NLD-PS-RTM", "name": "Rotterdam port-state approach zone (demo)", "authority": "Port of Rotterdam Harbour Master / ILT", "country": "NL", "zone_type": "port_state",
     "geometry": {"type": "Polygon", "coordinates": [[[3.6, 51.8], [4.3, 51.8], [4.3, 52.2], [3.6, 52.2], [3.6, 51.8]]]}},
]


async def seed_zones():
    await db.jurisdictions.create_index([("geometry", "2dsphere")])
    await db.jurisdictions.create_index("code", unique=True)
    if await db.jurisdictions.count_documents({}) > 0:
        return 0
    now = datetime.now(timezone.utc)
    await db.jurisdictions.insert_many([{**z, "id": new_id(), "active": True, "source": "demo-seed (simplified, not official boundaries)",
                                          "created_at": now, "updated_at": now} for z in DEMO_ZONES])
    return len(DEMO_ZONES)


async def resolve_jurisdictions(geometry: dict, centroid: dict):
    """Return zones intersecting the spill geometry, primary = highest-priority zone containing the centroid."""
    poly = validate_polygon(geometry)
    cpt = shape(centroid)
    zones = await db.jurisdictions.find({"active": True, "geometry": {"$geoIntersects": {"$geometry": geometry}}}, {"_id": 0}).to_list(200)
    out = []
    for z in zones:
        zg = shape(z["geometry"])
        if not zg.intersects(poly):
            continue
        overlap = zg.intersection(poly).area / poly.area if poly.area else 0.0
        out.append({"id": z["id"], "code": z["code"], "name": z["name"], "authority": z["authority"], "country": z.get("country"),
                    "zone_type": z["zone_type"], "zone_label": ZONE_LABELS.get(z["zone_type"], z["zone_type"]), "contains_centroid": zg.contains(cpt), "overlap_fraction": round(overlap, 3)})
    out.sort(key=lambda j: (not j["contains_centroid"], TYPE_PRIORITY.get(j["zone_type"], 9), -j["overlap_fraction"]))
    primary = out[0] if out and out[0]["contains_centroid"] else (out[0] if out else None)
    return out, primary


async def apply_to_case(case_id: str, actor="system"):
    case = await db.cases.find_one({"id": case_id}, {"_id": 0})
    spill = await db.spill_observations.find_one({"id": case["spill_observation_id"]}, {"_id": 0, "geometry": 1, "centroid": 1})
    zones, primary = await resolve_jurisdictions(spill["geometry"], spill["centroid"])
    await db.cases.update_one({"id": case_id}, {"$set": {"jurisdictions": zones, "primary_jurisdiction": primary, "jurisdiction_resolved_at": datetime.now(timezone.utc)}})
    await audit("case", case_id, "case.jurisdiction_resolved", {"primary": primary["code"] if primary else None, "zones": [z["code"] for z in zones]}, actor)
    return zones, primary
