from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user, require_role, ROLES
from db import db, clean, audit
from models import REASON_CODES, ATTRIBUTION_STATUSES, SPILL_QUALITY_FLAGS, AIS_QUALITY_FLAGS, CorrelationParams
from correlation import ALGORITHM_VERSION
from detector import model_info, DETECTOR_VERSION
import jobs
from storage import STORAGE_MODE
from ais_live import get_config as get_ais_config
from emailer import get_config as get_email_config

router = APIRouter()


@router.get("/jobs")
async def list_jobs(status: Optional[str] = None, limit: int = Query(100, le=500), user=Depends(get_current_user)):
    q = {"status": status} if status else {}
    return clean(await db.jobs.find(q, {"_id": 0}).sort("created_at", -1).to_list(limit))


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, user=Depends(get_current_user)):
    job = await db.jobs.find_one({"id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(404, "job not found")
    return clean(job)


@router.get("/alerts")
async def list_alerts(unacknowledged: bool = False, limit: int = Query(100, le=500), user=Depends(get_current_user)):
    q = {"acknowledged": False} if unacknowledged else {}
    return clean(await db.alerts.find(q, {"_id": 0}).sort("created_at", -1).to_list(limit))


@router.post("/alerts/{alert_id}/ack")
async def ack_alert(alert_id: str, user=Depends(require_role("supervisor"))):
    res = await db.alerts.find_one_and_update({"id": alert_id}, {"$set": {"acknowledged": True, "acknowledged_by": user["email"], "acknowledged_at": datetime.now(timezone.utc)}},
                                              projection={"_id": 0}, return_document=True)
    if not res:
        raise HTTPException(404, "alert not found")
    await audit("alert", alert_id, "alert.acknowledged", {"role": user["role"]}, user["email"])
    return clean(res)


@router.get("/audit")
async def list_audit(entity_id: Optional[str] = None, limit: int = Query(200, le=2000), user=Depends(get_current_user)):
    q = {"entity_id": entity_id} if entity_id else {}
    return clean(await db.audit_events.find(q, {"_id": 0}).sort("created_at", -1).to_list(limit))


@router.get("/config/defaults")
async def config_defaults(user=Depends(get_current_user)):
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "runtime": {"storage_mode": STORAGE_MODE, "job_workers": jobs.WORKER_COUNT, "job_max_attempts": jobs.MAX_ATTEMPTS, "job_lease_seconds": jobs.LEASE_SECONDS},
        "detector": model_info(),
        "detector_version": DETECTOR_VERSION,
        "correlation_params": CorrelationParams().model_dump(),
        "attribution_statuses": ATTRIBUTION_STATUSES,
        "spill_quality_flags": SPILL_QUALITY_FLAGS,
        "ais_quality_flags": AIS_QUALITY_FLAGS,
        "reason_codes": REASON_CODES,
        "roles": ROLES,
        "permissions": {"analyst": ["ingest", "correlate", "review", "export"], "supervisor": ["+acknowledge_alerts", "+override_cases"], "admin": ["+manage_users", "+reseed"]},
        "environment_provider": "open-meteo (ERA5 reanalysis / forecast wind 10 m; Copernicus Marine surface current)",
        "drift_model": "surface drift = 3% of wind speed (downwind) + surface current; wind direction is meteorological (FROM), current is oceanographic (TOWARD)",
    }


@router.get("/stats")
async def stats(user=Depends(get_current_user)):
    by_status = {s: await db.cases.count_documents({"attribution_status": s}) for s in ATTRIBUTION_STATUSES}
    return {
        "cases_total": await db.cases.count_documents({}),
        "by_attribution_status": by_status,
        "pending_review": await db.cases.count_documents({"review_state": "pending"}),
        "alerts_unacknowledged": await db.alerts.count_documents({"acknowledged": False}),
        "scenes": await db.scenes.count_documents({}),
        "spill_observations": await db.spill_observations.count_documents({}),
        "ais_positions": await db.ais_positions.estimated_document_count(),
        "jobs_running": await db.jobs.count_documents({"status": {"$in": ["queued", "running"]}}),
        "jobs_failed": await db.jobs.count_documents({"status": "failed"}),
    }
