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
- Architecture, quickstart, demo script, and model cards are documented under `docs/`.

## Real Sentinel-1 ML path
The repository contains the training/evaluation path for `sar_spill_seg_v2`, a dual-polarization **VV + VH U-Net semantic-segmentation model** intended to replace the synthetic baseline after real training and validation.

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

## Automatic detector selection
The production detector now prefers `sar_spill_seg_v2` when all of the following are true:

1. `SAR_SEG_ENABLE` is `auto` or `force`;
2. `checkpoints/sar_spill_seg_v2.pt` exists (mounted at `/models/sar_spill_seg_v2.pt` in Docker);
3. the backend was built with the optional PyTorch runtime;
4. the scene contains both VV and VH assets; and
5. the SAR radiometry is compatible with the v2 input contract.

Otherwise Varuna-Netra automatically falls back to `sar_spill_pixel_v1` and records the fallback reason in the detector result/audit trail. Raw Sentinel-1 GRD amplitude is deliberately not passed into the Sigma0-dB-trained v2 model. Sentinel-1 RTC is available as a calibrated dual-pol source, but its Gamma0-to-Sigma0 domain difference is explicitly flagged for analyst review.

After a checkpoint has been trained and independently evaluated, enable the optional Docker inference runtime with:

```bash
# .env
INSTALL_SAR_SEG_RUNTIME=true
SAR_SEG_ENABLE=auto
```

Then rebuild:

```bash
docker compose up -d --build
```

The API root (`GET /api/`) reports whether v2 is loaded, unavailable, or falling back. `SAR_SEG_ENABLE=force` can be used for validation/deployment checks where a missing or invalid v2 checkpoint should fail startup instead of silently using v1.

## Important ML limitation
Until `sar_spill_seg_v2` has actually been trained and independently evaluated on Part III, do not claim real-world model accuracy. The current bundled `sar_spill_pixel_v1` remains a synthetic-trained offline baseline. All automated detections remain **ML candidates requiring analyst review**.

## Live integrations
Set `AIS_MODE=live` with `AISSTREAM_API_KEY` for live AISStream. Set `EMAIL_MODE=live` with `RESEND_API_KEY` and `SENDER_EMAIL` for live email.
