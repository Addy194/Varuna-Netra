# sar_spill_seg_v2 — promotion and real-scene verification

This guide begins **after** the real Sentinel-1 training pipeline has produced a candidate checkpoint and independent Part III metrics.

## 1. Train and independently evaluate

```bash
python backend/training/run_real_sar_pipeline.py
```

Expected outputs:

```text
checkpoints/sar_spill_seg_v2.pt
checkpoints/sar_spill_seg_v2_test_metrics.json
```

The Part III metrics JSON is bound to the exact checkpoint bytes with SHA-256. Do not rename or replace a checkpoint and reuse an older metrics file.

## 2. Review the independent metrics

Only quote results from `sar_spill_seg_v2_test_metrics.json` in SIH material. Review at minimum:

- Dice and IoU;
- precision and recall;
- F1;
- no-oil scene false-positive rate;
- look-alike scene false-positive rate.

The repository does not hard-code a fake acceptance threshold. Project-specific gates can be supplied during promotion after the team reviews the real results.

## 3. Promote the exact evaluated checkpoint

Basic integrity/provenance gate:

```bash
python backend/training/promote_segmentation_checkpoint.py \
  --checkpoint checkpoints/sar_spill_seg_v2.pt \
  --metrics checkpoints/sar_spill_seg_v2_test_metrics.json
```

Optional project-defined quality gates can be added, for example with `--min-dice`, `--min-recall`, `--max-lookalike-fpr`, and `--max-no-oil-fpr`. Choose these from deployment requirements; do not invent values merely to pass a demo.

The promotion script verifies:

- `model_id == sar_spill_seg_v2`;
- dual-polarization VV+VH model input;
- declared Sentinel-1 Sigma0 training representation;
- the metrics file SHA-256 matches the exact checkpoint bytes;
- the independent test DOI is Part III (`10.5281/zenodo.13761290`);
- the test protocol is the sealed full-scene sliding-window protocol;
- at least 150 oil, 150 no-oil and 150 look-alike Part III scenes were evaluated;
- required segmentation and scene-level false-positive metrics exist.

Successful promotion writes:

```text
checkpoints/sar_spill_seg_v2.deployment.json
```

Promotion means the checkpoint passed the repository's integrity/evaluation gate. It is **not** government or operational certification, and analyst review remains required.

The one-command runner can also invoke the promotion gate after evaluation:

```bash
python backend/training/run_real_sar_pipeline.py --stage evaluate --promote
```

## 4. Enable v2 in Docker

Set these values in your local `.env`:

```env
INSTALL_SAR_SEG_RUNTIME=true
SAR_SEG_ENABLE=auto
SAR_SEG_CHECKPOINT=/checkpoints/sar_spill_seg_v2.pt
```

For Sentinel-1 RTC asset access, configure your Planetary Computer subscription key outside Git:

```env
PC_SDK_SUBSCRIPTION_KEY=<your key>
```

Never commit real credentials.

Build and start the stack:

```bash
docker compose up -d --build
```

Check runtime status:

```bash
curl http://localhost:8000/api/
```

The `sar_segmentation` object should report the checkpoint as present and `loaded: true`. In `SAR_SEG_ENABLE=auto`, a missing/invalid runtime falls back to v1. For pre-deployment validation, `SAR_SEG_ENABLE=force` turns a missing or broken v2 checkpoint into a startup failure instead of a silent fallback.

## 5. Real Sentinel-1 RTC verification

The Scene Explorer now preserves the map/search box as `analysis_bbox`. v2 reads only that investigation window from the remote VV and VH cloud-optimized rasters instead of attempting to load a full Sentinel-1 swath.

You can verify the deployed path from the UI by choosing **Sentinel-1 RTC**, zooming to a small offshore AOI, searching, and selecting **Register + detect**.

Or run the automated verifier:

```bash
python backend/training/verify_live_sar_v2.py
```

Its default AOI is a small offshore Mumbai demonstration box. You can choose another small WGS84 box:

```bash
python backend/training/verify_live_sar_v2.py \
  --bbox 72.45 18.70 72.65 18.90 \
  --days 90
```

Provide the analyst password through `DEMO_ANALYST_PASSWORD` or enter it at the secure prompt. The script does not print the access token.

The verifier checks that:

1. the API is reachable;
2. `sar_spill_seg_v2` is loaded;
3. a dual-polarization Sentinel-1 RTC scene can be found;
4. the scene is registered with the requested AOI;
5. VV+VH data are read through the windowed raster path;
6. detection is handled by `sar_spill_seg_v2`, not the fallback model.

A result with zero candidate slicks is still a valid integration check. It proves the real data path worked; it says nothing by itself about accuracy.

## Radiometry note

The selected training dataset is Sentinel-1 **Sigma0 dB**. Planetary Computer Sentinel-1 RTC is treated by the runtime as calibrated Gamma0 linear data and converted to dB, while explicitly recording a Gamma0-vs-Sigma0 domain-shift flag. This is safer than pretending the domains are identical.

For a stronger operational model, align training and inference radiometry exactly: either train/calibrate on the same RTC representation used at runtime or add a validated Sentinel-1 GRD preprocessing chain that reproduces the training representation. Until then, retain the domain-shift warning and analyst-review requirement.
