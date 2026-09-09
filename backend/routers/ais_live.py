from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import get_current_user, require_role
from db import db, clean, audit
import ais_live

router = APIRouter()


def _mask(k: str) -> str:
    return f"{k[:4]}…{k[-4:]}" if k and len(k) > 10 else ("set" if k else "")


@router.get("/ais/live/status")
async def live_status(user=Depends(get_current_user)):
    cfg = await ais_live.get_config()
    return clean({**ais_live.status(), "configured": bool(cfg.get("api_key")) or cfg.get("mode") == "demo", "mode": cfg.get("mode"), "enabled": cfg["enabled"], "bboxes": cfg["bboxes"], "source": cfg["source"], "api_key": _mask(cfg["api_key"]),
                  "provider": "aisstream.io (free key at aisstream.io)", "updated_at": cfg["updated_at"], "updated_by": cfg["updated_by"]})


class LiveSettings(BaseModel):
    mode: Optional[str] = None
    api_key: Optional[str] = None
    enabled: Optional[bool] = None
    bboxes: Optional[List[List[List[float]]]] = None


@router.put("/ais/live/settings")
async def live_settings(body: LiveSettings, user=Depends(require_role("admin"))):
    update = body.model_dump(exclude_none=True)
    if "mode" in update and update["mode"] not in ("demo", "live"): raise HTTPException(400, "mode must be demo or live")
    if update.get("api_key"): update["mode"] = "live"
    if "api_key" in update and update["api_key"] == "":
        update["api_key"] = None
        update["mode"] = "demo"
    if "bboxes" in update:
        if not update["bboxes"] or len(update["bboxes"]) > 10:
            raise HTTPException(400, "provide 1–10 bounding boxes")
        for bb in update["bboxes"]:
            if len(bb) != 2 or any(len(c) != 2 or not (-90 <= c[0] <= 90 and -180 <= c[1] <= 180) for c in bb):
                raise HTTPException(400, "each bbox must be [[lat,lon],[lat,lon]]")
    update.update({"updated_at": datetime.now(timezone.utc), "updated_by": user["email"]})
    await db.settings.update_one({"key": "ais_live"}, {"$set": update}, upsert=True)
    await audit("settings", "ais_live", "settings.ais_live_updated", {k: ("***" if k == "api_key" else v) for k, v in update.items() if k != "updated_at"}, user["email"])
    ais_live.start()
    return await live_status(user)
