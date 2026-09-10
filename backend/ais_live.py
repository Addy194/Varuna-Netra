"""AISStream live worker — genuine WebSocket client, no simulated vessels, no silent demo fallback.
Single authoritative coverage source: settings.ais_coverage (mode spill|scene|manual|default). AISStream bbox format: [[[lat,lon],[lat,lon]]]."""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import websockets

from db import db
from models import AISPositionIn

logger = logging.getLogger("ais_live")
WS_URL = "wss://stream.aisstream.io/v0/stream"
SOURCE = "AISStream"
FILTER_TYPES = ["PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport", "ShipStaticData", "StaticDataReport"]
STALE_MIN, MARGIN_KM, BACKOFF = 30, 100.0, [1, 2, 4, 8, 16, 30]

# Regional defaults (S, W, N, E) — verified against AISStream docs: BoundingBoxes = [[[lat_min, lon_min], [lat_max, lon_max]]]
REGIONS = {
    "west_coast": {"name": "Arabian Sea / West Coast", "bbox": [8.0, 66.0, 24.5, 76.5]},
    "east_coast": {"name": "Bay of Bengal / East Coast", "bbox": [10.0, 78.5, 22.5, 90.0]},
    "south_india": {"name": "Southern India / Indian Ocean", "bbox": [5.5, 72.0, 10.5, 81.0]},
    "andaman_nicobar": {"name": "Andaman & Nicobar", "bbox": [5.5, 90.0, 15.0, 95.5]},
}
DEFAULT_REGIONS = list(REGIONS)

state = {"connected": False, "subscription_confirmed": False, "messages": 0, "positions": 0, "inserted": 0, "last_message_at": None, "last_position_at": None,
         "connected_at": None, "last_disconnect_at": None, "error": None, "reconnects": 0, "msg_times": [], "active": {}}
_task: Optional[asyncio.Task] = None
_buffer: list = []
_reconnect_event = asyncio.Event()


def key_configured() -> bool:
    return bool(os.environ.get("AISSTREAM_API_KEY"))


def validate_bbox(south: float, west: float, north: float, east: float) -> list:
    if not (-90 <= south <= 90 and -90 <= north <= 90 and -180 <= west <= 180 and -180 <= east <= 180):
        raise ValueError("coordinates out of range")
    if south >= north or west >= east:
        raise ValueError("south must be < north and west < east")
    return [south, west, north, east]


def to_aisstream_boxes(bboxes: list) -> list:
    """[S,W,N,E] → AISStream [[lat_min,lon_min],[lat_max,lon_max]]."""
    return [[[b[0], b[1]], [b[2], b[3]]] for b in bboxes]


def expand_bbox(south, west, north, east, margin_km: float = MARGIN_KM) -> list:
    import math
    dlat = margin_km / 110.574
    dlon = margin_km / (111.32 * max(math.cos(math.radians((south + north) / 2)), 0.1))
    return validate_bbox(max(-90, south - dlat), max(-180, west - dlon), min(90, north + dlat), min(180, east + dlon))


async def get_coverage() -> dict:
    default = {"mode": "default", "name": "Indian coastal regions", "bboxes": [REGIONS[r]["bbox"] for r in DEFAULT_REGIONS], "regions": DEFAULT_REGIONS}
    s = await db.settings.find_one({"key": "ais_coverage"}, {"_id": 0})
    if not s:
        return default
    if s.get("mode") == "spill" and s.get("ref") and not await db.cases.find_one({"id": s["ref"]}, {"_id": 1}):
        await db.settings.delete_one({"key": "ais_coverage"})  # followed spill was deleted → revert to regional defaults
        return default
    return s


async def set_coverage(bboxes: list, mode: str, name: str, actor: str = "system", ref: Optional[str] = None) -> dict:
    doc = {"key": "ais_coverage", "mode": mode, "name": name, "bboxes": [validate_bbox(*b) for b in bboxes], "ref": ref, "updated_at": datetime.now(timezone.utc), "updated_by": actor}
    await db.settings.update_one({"key": "ais_coverage"}, {"$set": doc}, upsert=True)
    _reconnect_event.set()
    return doc


async def coverage_for_spill(bbox_swne: list, case_id: str) -> Optional[dict]:
    """Automatic: follow a new real spill unless an operator pinned a manual AOI."""
    cur = await get_coverage()
    if cur.get("mode") == "manual":
        return None
    return await set_coverage([expand_bbox(*bbox_swne)], "spill", f"spill investigation {case_id[:8]} (+{MARGIN_KM:.0f} km)", "system", case_id)


def _parse_time(s: str) -> datetime:
    try:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return datetime.now(timezone.utc)


def decode_frame(raw) -> Optional[dict]:
    """AISStream may send text or binary UTF-8 JSON frames; malformed frames are skipped."""
    try:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        msg = json.loads(raw)
        return msg if isinstance(msg, dict) else None
    except Exception:  # noqa: BLE001
        return None


def to_position(msg: dict) -> Optional[AISPositionIn]:
    """PositionReport / Class B (Standard, Extended) → AISPositionIn (existing ais_positions schema). Never invents values."""
    body = msg.get("Message") or {}
    pr = body.get("PositionReport") or body.get("StandardClassBPositionReport") or body.get("ExtendedClassBPositionReport")
    if not pr:
        return None
    meta = msg.get("MetaData") or {}
    lat, lon = pr.get("Latitude", meta.get("Latitude")), pr.get("Longitude", meta.get("Longitude"))
    if lat is None or lon is None or not (-90 <= lat <= 90) or not (-180 <= lon <= 180) or lat == 91 or lon == 181:
        return None
    mmsi = pr.get("UserID") or meta.get("MMSI")
    if not mmsi:
        return None
    sog, cog, hdg = pr.get("Sog"), pr.get("Cog"), pr.get("TrueHeading")
    return AISPositionIn(mmsi=str(mmsi), vessel_name=(meta.get("ShipName") or "").strip() or None, timestamp=_parse_time(meta.get("time_utc") or ""), lat=lat, lon=lon,
                         sog_kn=sog if sog is not None and sog < 102.3 else None, cog_deg=cog if cog is not None and cog < 360 else None,
                         heading_deg=hdg if hdg is not None and hdg <= 511 else None, source=SOURCE)


def _touch_active(p: AISPositionIn, nav_status) -> None:
    state["active"][p.mmsi] = {"mmsi": p.mmsi, "ship_name": p.vessel_name, "lat": p.lat, "lon": p.lon, "sog": p.sog_kn, "cog": p.cog_deg, "heading": p.heading_deg,
                               "navigation_status": nav_status, "timestamp": p.timestamp, "received_at": datetime.now(timezone.utc)}
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STALE_MIN)
    for k in [k for k, v in state["active"].items() if v["received_at"] < cutoff]:
        state["active"].pop(k, None)


def messages_per_min() -> int:
    now = time.time()
    state["msg_times"] = [t for t in state["msg_times"] if now - t <= 60]
    return len(state["msg_times"])


async def _flush() -> None:
    global _buffer
    if not _buffer:
        return
    batch, _buffer = _buffer, []
    from services import ingest_ais
    try:
        res = await ingest_ais(batch, source_batch_id=f"aisstream-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}", actor=SOURCE)
        state["inserted"] += res["inserted"]
    except Exception as e:  # noqa: BLE001
        logger.error("aisstream flush failed: %s", e)


def _handle(msg: dict) -> None:
    state["messages"] += 1
    state["last_message_at"] = datetime.now(timezone.utc)
    state["msg_times"].append(time.time())
    if msg.get("error"):
        raise RuntimeError(f"AISStream error: {msg['error']}")
    if msg.get("MessageType") == "SubscriptionConfirmation" or not state["subscription_confirmed"]:
        state["subscription_confirmed"] = True  # any accepted data frame proves the subscription was accepted
        state["connected"] = True
    p = to_position(msg)
    if p:
        _buffer.append(p)
        state["positions"] += 1
        state["last_position_at"] = state["last_message_at"]
        nav = ((msg.get("Message") or {}).get("PositionReport") or {}).get("NavigationalStatus")
        _touch_active(p, nav)


async def _session(key: str, boxes: list) -> None:
    async with websockets.connect(WS_URL, ping_interval=20, close_timeout=5, max_size=2**22) as ws:
        await ws.send(json.dumps({"APIKey": key, "BoundingBoxes": boxes, "FilterMessageTypes": FILTER_TYPES}))
        state.update({"connected_at": datetime.now(timezone.utc), "error": None})
        logger.info("aisstream websocket open, subscription sent for %d bbox(es)", len(boxes))
        last_flush = time.time()
        while not _reconnect_event.is_set():
            try:
                msg = decode_frame(await asyncio.wait_for(ws.recv(), timeout=10))
                if msg:
                    _handle(msg)
            except asyncio.TimeoutError:
                pass
            if time.time() - last_flush >= 8 or len(_buffer) >= 500:
                await _flush()
                last_flush = time.time()
        await _flush()


async def _run() -> None:
    attempt = 0
    logger.info("AISStream API key configured: %s", key_configured())
    while True:
        _reconnect_event.clear()
        key = os.environ.get("AISSTREAM_API_KEY")
        if not key:
            state.update({"connected": False, "subscription_confirmed": False, "error": "API key not configured"})
            try:
                await asyncio.wait_for(_reconnect_event.wait(), timeout=15)
            except asyncio.TimeoutError:
                pass
            continue
        cov = await get_coverage()
        try:
            await _session(key, to_aisstream_boxes(cov["bboxes"]))
            attempt = 0  # clean coverage change → immediate reconnect
        except Exception as e:  # noqa: BLE001
            state.update({"connected": False, "subscription_confirmed": False, "error": str(e)[:300], "reconnects": state["reconnects"] + 1, "last_disconnect_at": datetime.now(timezone.utc)})
            delay = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            attempt += 1
            logger.warning("aisstream disconnected (%s); reconnect in %ss", str(e)[:120], delay)
            await asyncio.sleep(delay)
        finally:
            state.update({"connected": False, "subscription_confirmed": False})


def start() -> None:
    """Idempotent: exactly one worker per process."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_run())


def stop() -> None:
    """Cancel the worker so the process shuts down promptly (supervisor SIGTERM → no 20 s SIGKILL wait)."""
    if _task and not _task.done():
        _task.cancel()


def connection_state() -> str:
    """NOT_CONFIGURED | CONNECTING | CONNECTED (socket+subscription, no data yet) | LIVE (genuine messages <2 min) | RECONNECTING | OFFLINE."""
    if not key_configured():
        return "NOT_CONFIGURED"
    now = datetime.now(timezone.utc)
    if state["connected"] and state["subscription_confirmed"]:
        recent = state["last_position_at"] and (now - state["last_position_at"]).total_seconds() < 300
        return "LIVE" if (recent and state["positions"] > 0) else "CONNECTED"
    if state["error"] and state["error"] != "API key not configured":
        return "RECONNECTING" if state["reconnects"] and state["last_disconnect_at"] and (now - state["last_disconnect_at"]).total_seconds() < 120 else "OFFLINE"
    return "CONNECTING" if (_task and not _task.done()) else "OFFLINE"


def status() -> dict:
    """Runtime telemetry only — nothing hard-coded, key never included."""
    return {"source": SOURCE, "mode": "live", "state": connection_state(), "configured": key_configured(), "connected": bool(state["connected"] and state["subscription_confirmed"]),
            "websocket_open": bool(state["connected_at"] and not state["last_disconnect_at"] or (state["connected_at"] and state["last_disconnect_at"] and state["connected_at"] > state["last_disconnect_at"])),
            "subscription_confirmed": state["subscription_confirmed"], "messages_received": state["messages"], "positions_parsed": state["positions"], "positions_stored": state["inserted"],
            "messages_per_min": messages_per_min(), "vessels_active": len(state["active"]), "last_message_at": state["last_message_at"], "last_position_at": state["last_position_at"],
            "reconnects": state["reconnects"], "last_disconnect_at": state["last_disconnect_at"], "error": state["error"], "worker_running": bool(_task and not _task.done()),
            "reason": None if (state["connected"] and state["subscription_confirmed"]) else (
                "API key not configured" if not key_configured() else
                "WebSocket authentication failed (AISStream rejected the API key)" if state["error"] and ("api key" in str(state["error"]).lower() or "1008" in str(state["error"])) else
                "Connection lost" if state["error"] else
                "No AIS messages received recently" if state["connected_at"] and state["last_message_at"] and (datetime.now(timezone.utc) - state["last_message_at"]).total_seconds() > 120 else "Connecting")}


async def test_connection(timeout_s: float = 12.0) -> dict:
    """Diagnostic chain: key → websocket → subscription → confirmation/first message. Never returns the key."""
    out = {"configured": key_configured(), "websocket": False, "subscription": False, "message_received": False, "latency_ms": None, "error": None}
    if not out["configured"]:
        out["error"] = "API key not configured"
        return out
    t = time.time()
    try:
        cov = await get_coverage()
        async with websockets.connect(WS_URL, close_timeout=3, max_size=2**22) as ws:
            out["websocket"] = True
            await ws.send(json.dumps({"APIKey": os.environ["AISSTREAM_API_KEY"], "BoundingBoxes": to_aisstream_boxes(cov["bboxes"]), "FilterMessageTypes": FILTER_TYPES}))
            out["subscription"] = True
            msg = decode_frame(await asyncio.wait_for(ws.recv(), timeout=timeout_s))
            if msg and msg.get("error"):
                out["error"] = f"AISStream error: {msg['error']}"
            else:
                out["message_received"] = bool(msg)
        out["latency_ms"] = int((time.time() - t) * 1000)
    except asyncio.TimeoutError:
        out["error"] = "no AIS message within timeout (coverage may be empty right now)"
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:200]
    return out
