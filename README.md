# Varuna-Netra

Decision-support platform for satellite oil-spill candidate detection and vessel correlation. This release is packaged for reproducible local/demo execution and no longer depends on Emergent runtime services.

## Quick start
```bash
cp .env.example .env
docker compose up --build
```
Frontend: http://localhost:3000 · API: http://localhost:8000/docs

## What changed
- Bundled SAR ML candidate detector (`sar_spill_pixel_v1`) replaces the mock/Otsu detector entry point.
- Sentinel-1 VV/VH assets are retained from STAC registration; rendered preview remains a fallback.
- Manual scenes without imagery use a clearly labelled deterministic synthetic demo input.
- AIS supports demo replay; live AISStream remains opt-in.
- Email supports a Mongo-backed demo outbox; Resend remains opt-in.
- Jobs use a MongoDB-backed durable queue with leases/retries and multiple workers.
- Local filesystem storage is the default; external object storage is optional.
- Architecture, quickstart, demo script, and model card are documented under `docs/`.

## Important ML limitation
The bundled model is trained on synthetic SAR-like chips so that the repository is runnable offline. Its bundled holdout metrics are synthetic-demo metrics only. Real Sentinel-1 labelled validation is still required before operational deployment; the UI and API therefore label outputs as **ML candidates requiring analyst review**.

## Live integrations
Set `AIS_MODE=live` with `AISSTREAM_API_KEY` for live AISStream. Set `EMAIL_MODE=live` with `RESEND_API_KEY` and `SENDER_EMAIL` for live email.
