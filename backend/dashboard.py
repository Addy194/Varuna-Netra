"""Canonical dashboard counters — pure MongoDB aggregation over live records. Demo/seed/test records are identified (never deleted) and excluded."""
from datetime import datetime, timezone

DEMO_SOURCES = ["mock_detector", "demo", "test", "seed"]
# origin: "detector" (automated candidate on a real Sentinel-1 scene) | "analyst" (registered with a real scene) | "imported" (API-registered polygon, no Sentinel scene — unverified) | "demo"
REAL_ORIGINS = ["detector", "analyst"]
REAL_CASE_FILTER = {"source": {"$nin": DEMO_SOURCES}, "is_demo": {"$ne": True}, "origin": {"$in": REAL_ORIGINS}}
DEMO_CASE_FILTER = {"$or": [{"source": {"$in": DEMO_SOURCES}}, {"is_demo": True}, {"origin": "demo"}]}
PENDING_FILTER = {"status": "open", "review_state": "pending"}
ORIGIN_LABEL = {"detector": "LIVE DETECTED", "analyst": "ANALYST CREATED", "imported": "IMPORTED HISTORICAL", "demo": "DEMO / REFERENCE", "reference": "DEMO / REFERENCE"}
CORRELATION_STATE_LABEL = {"NOT_ANALYZED": "NOT ANALYZED", "NO_AIS_COVERAGE": "NO AIS COVERAGE", "NO_CANDIDATE_IN_TIME_WINDOW": "NO CANDIDATE IN TIME WINDOW", "SCORED": "SCORED"}


def confidence_source(case: dict) -> str:
    """detector = value produced by the SAR dark-spot detector; registrant = number typed in at registration (API/analyst) — not a detector output."""
    return "detector" if case.get("source") == "dark_spot_detector" else "registrant"


def correlation_state(case: dict, result: dict | None) -> str:
    if not case.get("latest_result_version") or not result:
        return "NOT_ANALYZED"
    if not result.get("position_count"):
        return "NO_AIS_COVERAGE"
    if not result.get("vessel_count") or not case.get("candidate_count"):
        return "NO_CANDIDATE_IN_TIME_WINDOW"
    return "SCORED"


async def annotate_cases(db, rows: list) -> list:
    ids = [c["id"] for c in rows if c.get("latest_result_version")]
    latest = {}
    if ids:
        async for r in db.correlation_results.find({"case_id": {"$in": ids}}, {"_id": 0, "case_id": 1, "version": 1, "position_count": 1, "vessel_count": 1}).sort("version", -1):
            latest.setdefault(r["case_id"], r)
    for c in rows:
        st = correlation_state(c, latest.get(c["id"]))
        c["correlation_state"], c["correlation_state_label"] = st, CORRELATION_STATE_LABEL[st]
        c["detection_confidence_source"] = confidence_source(c)
        c["origin_label"] = ORIGIN_LABEL.get(c.get("origin"), c.get("origin_label"))
    return rows

SEMANTICS = {
    "live_cases": "open cases with origin detector/analyst (real Sentinel-1 scene attached at registration); imported API polygons and demo/seed records excluded",
    "pending_review": "live cases with status=open AND review_state=pending (no analyst decision yet)",
    "probable_confirmed": "live cases with attribution_status probable or analyst_confirmed",
    "ais_fixes_indexed": "persisted ais_positions documents (estimated count)",
    "alerts": "unacknowledged alerts not demo-flagged and not attached to a demo/imported case",
}


def classify_origin(case: dict, spill: dict | None, actor: str | None = None) -> str:
    if case.get("is_demo") or case.get("source") in DEMO_SOURCES or (actor or "").lower() == "seed":
        return "demo"
    pv = ((spill or {}).get("processing_version") or ((spill or {}).get("raw_input") or {}).get("processing_version") or "")
    if not case.get("scene_id"):
        return "imported"  # no Sentinel scene attached at registration — polygon supplied via API/form, unverified
    if case.get("source") == "dark_spot_detector" or "darkspot" in pv:
        return "detector"
    return "analyst"


async def tag_origins(db) -> dict:
    """Idempotent: tags untagged cases with a provenance origin (never deletes). Seed actor discovered from the audit trail."""
    seed_ids = {a["entity_id"] for a in await db.audit_events.find({"action": "case.opened", "actor": "seed"}, {"_id": 0, "entity_id": 1}).to_list(5000)}
    counts = {}
    async for c in db.cases.find({"origin": {"$exists": False}}, {"_id": 0, "id": 1, "source": 1, "scene_id": 1, "is_demo": 1, "spill_observation_id": 1}):
        sp = await db.spill_observations.find_one({"id": c["spill_observation_id"]}, {"_id": 0, "processing_version": 1, "raw_input.processing_version": 1})
        o = classify_origin(c, sp, "seed" if c["id"] in seed_ids else None)
        await db.cases.update_one({"id": c["id"]}, {"$set": {"origin": o, "origin_label": ORIGIN_LABEL[o], "origin_tagged_at": datetime.now(timezone.utc)}})
        counts[o] = counts.get(o, 0) + 1
    return counts


async def demo_case_ids(db):
    return [c["id"] for c in await db.cases.find(DEMO_CASE_FILTER, {"_id": 0, "id": 1}).to_list(10000)]


def real_alert_filter(demo_ids, unacknowledged=None):
    q = {"is_demo": {"$ne": True}, "case_id": {"$nin": demo_ids}}
    if unacknowledged is not None:
        q["acknowledged"] = not unacknowledged
    return q


async def compute_summary(db):
    demo_ids = await demo_case_ids(db)
    excluded_ids = [c["id"] for c in await db.cases.find({"origin": {"$nin": REAL_ORIGINS}}, {"_id": 0, "id": 1}).to_list(10000)]
    cases, alerts = db.cases, db.alerts
    unread = real_alert_filter(excluded_ids, unacknowledged=True)
    by_attr = {r["_id"]: r["n"] for r in await cases.aggregate([{"$match": REAL_CASE_FILTER}, {"$group": {"_id": "$attribution_status", "n": {"$sum": 1}}}]).to_list(20)}
    by_review = {r["_id"]: r["n"] for r in await cases.aggregate([{"$match": REAL_CASE_FILTER}, {"$group": {"_id": "$review_state", "n": {"$sum": 1}}}]).to_list(20)}
    by_source = {r["_id"]: r["n"] for r in await cases.aggregate([{"$match": REAL_CASE_FILTER}, {"$group": {"_id": "$source", "n": {"$sum": 1}}}]).to_list(50)}
    by_origin = {(r["_id"] or "untagged"): r["n"] for r in await cases.aggregate([{"$group": {"_id": "$origin", "n": {"$sum": 1}}}]).to_list(10)}
    total = await cases.count_documents(REAL_CASE_FILTER)
    open_ = await cases.count_documents({**REAL_CASE_FILTER, "status": "open"})
    pending = await cases.count_documents({**REAL_CASE_FILTER, **PENDING_FILTER})
    prob = await cases.count_documents({**REAL_CASE_FILTER, "attribution_status": {"$in": ["probable", "analyst_confirmed"]}})
    ais_n = await db.ais_positions.estimated_document_count()
    return {
        "live_cases": open_, "pending_review": pending, "probable_confirmed": prob, "ais_fixes_indexed": ais_n,
        "cases": {"total": total, "open": open_, "closed": total - open_, "by_review_state": by_review, "by_attribution_status": by_attr, "by_source": by_source, "by_origin": by_origin, "all_records": await cases.count_documents({})},
        "pending": {"total": pending, "definition": SEMANTICS["pending_review"]},
        "alerts": {"total": await alerts.count_documents(real_alert_filter(excluded_ids)), "unread": await alerts.count_documents(unread),
                   "critical": await alerts.count_documents({**unread, "severity": {"$in": ["high", "critical"]}}), "definition": SEMANTICS["alerts"]},
        "jobs": {"running": await db.jobs.count_documents({"status": {"$in": ["queued", "running"]}}), "failed": await db.jobs.count_documents({"status": "failed"})},
        "observations": {"spill_observations": await db.spill_observations.count_documents({"is_demo": {"$ne": True}}), "scenes": await db.scenes.count_documents({}), "ais_positions": ais_n},
        "demo": {"cases": len(demo_ids), "imported": by_origin.get("imported", 0), "alerts": await alerts.count_documents({"$or": [{"is_demo": True}, {"case_id": {"$in": excluded_ids}}]}), "filter": f"origin not in {REAL_ORIGINS}, or source in {DEMO_SOURCES}, or is_demo=true"},
        "semantics": SEMANTICS, "source": "database", "updated_at": datetime.now(timezone.utc),
    }
