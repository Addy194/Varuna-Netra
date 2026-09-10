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

## Real Sentinel-1 ML path
The repository now also contains the training/evaluation path for `sar_spill_seg_v2`, a dual-polarization **VV + VH U-Net semantic-segmentation model** intended to replace the synthetic baseline after real training and validation.

The selected public dataset is the three-part Sentinel-1 SAR oil-spill dataset by Trujillo-Acatitla et al.:

- Part I — oil-spill train/validation: DOI `10.5281/zenodo.8346860`
- Part II — no-oil and look-alike train/validation: DOI `10.5281/zenodo.8253899`
- Part III — untouched independent test set: DOI `10.5281/zenodo.13761290`

After installing `backend/training/requirements-ml.txt`, the complete data-to-metrics workflow is:

```bash
python backend/training/run_real_sar_pipeline.py
```

That command performs resumable downloads, verifies the published Zenodo MD5 checksums, extracts the archives while deleting completed compressed files by default to control disk use, discovers the extracted directory layout, trains the model, and evaluates the frozen best checkpoint on Part III.

Training code is under `backend/training/`. It splits **complete source scenes before tile generation** to avoid train/validation leakage, uses both SAR polarizations, varies training crops across epochs while keeping validation reproducible, enables CUDA mixed precision when available, and evaluates Dice, IoU, precision, recall, F1 and false-positive scene rates. Dataset TIFFs and checkpoints remain outside GitHub. See `docs/REAL_SAR_TRAINING.md`.

## Important ML limitation
The detector currently bundled with the production/demo backend is still `sar_spill_pixel_v1`, trained on synthetic SAR-like chips so that the repository remains runnable offline. Its bundled holdout metrics are synthetic-demo metrics only. The new `sar_spill_seg_v2` code is a real-data training pipeline, **not a claim that a real-data checkpoint has already been trained**. Until a checkpoint is trained and independently evaluated, the UI and API must continue to label automated detections as **ML candidates requiring analyst review**.

## Live integrations
Set `AIS_MODE=live` with `AISSTREAM_API_KEY` for live AISStream. Set `EMAIL_MODE=live` with `RESEND_API_KEY` and `SENDER_EMAIL` for live email.
