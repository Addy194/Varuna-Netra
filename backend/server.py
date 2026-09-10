import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, APIRouter, Depends
from starlette.middleware.cors import CORSMiddleware

from db import db, client, ensure_indexes
import jobs
import services  # noqa: F401  (registers job handlers)
from routers import ingest, cases, system, auth as auth_router, jurisdictions, watchlist, timeline, attachments, rules as rules_router, satellite, ais_live as ais_live_router, scene_watch as scene_watch_router, imagery, live, gazetteer, archive, prosecution, icg as icg_router, vulnerability as vulnerability_router, dark_vessel as dark_vessel_router
from icg import seed_icg
from vulnerability import seed_sites
import ais_live
import scene_watch  # noqa: F401  (registers scene_watch_poll job handler)
from storage import init_storage, storage_available
from auth import seed_users, require_role
from jurisdiction import seed_zones, apply_to_case
import marine_regions  # noqa: F401  (registers import_eez job handler)
from seed import seed_demo
from correlation import ALGORITHM_VERSION

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("varunanetra")


def demo_mode_enabled() -> bool:
    return os.environ.get("DEMO_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ensure_indexes()
    await seed_users()
    await gazetteer.seed_gazetteer()
    await archive.seed_archive()
    zones_added = await seed_zones()
    await seed_icg()
    await seed_sites()
    jobs.start()
    ais_live.start()
    if storage_available():
        try:
            await asyncio.to_thread(init_storage)
            logger.info("object storage initialised")
        except Exception as e:  # noqa: BLE001
            logger.error("object storage init failed: %s", e)
    if demo_mode_enabled():
        try:
            res = await seed_demo()
            logger.warning("DEMO_MODE enabled; seed: %s", res)
            if zones_added:
                for c in await db.cases.find({"primary_jurisdiction": {"$exists": False}}, {"id": 1}).to_list(1000):
                    await apply_to_case(c["id"], "system")
        except Exception:
            logger.exception("demo seed failed")
    else:
        logger.info("DEMO_MODE disabled; using live/real data sources only")
    yield
    client.close()


app = FastAPI(title="VarunaNetra — Oil-Spill Detection & Vessel Correlation", version="0.2.0", lifespan=lifespan)
api = APIRouter(prefix="/api")


@api.get("/")
async def root():
    return {
        "service": "varunanetra",
        "algorithm_version": ALGORITHM_VERSION,
        "status": "ok",
        "mode": "demo" if demo_mode_enabled() else "real-data",
    }


@api.post("/seed")
async def reseed(user=Depends(require_role("admin"))):
    if not demo_mode_enabled():
        return {"status": "disabled", "message": "Demo seeding is disabled in real-data mode."}
    return await seed_demo()


api.include_router(auth_router.router)
api.include_router(jurisdictions.router)
api.include_router(watchlist.router)
api.include_router(timeline.router)
api.include_router(attachments.router)
api.include_router(rules_router.router)
api.include_router(satellite.router)
api.include_router(ais_live_router.router)
api.include_router(scene_watch_router.router)
api.include_router(imagery.router)
api.include_router(live.router)
api.include_router(icg_router.router)
api.include_router(vulnerability_router)
api.include_router(dark_vessel_router.router)
api.include_router(gazetteer.router)
api.include_router(archive.router)
api.include_router(prosecution.router)
api.include_router(ingest.router)
api.include_router(cases.router)
api.include_router(system.router)
app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
