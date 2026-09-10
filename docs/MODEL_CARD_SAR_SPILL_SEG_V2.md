# Model card — sar_spill_seg_v2

**Status:** training/evaluation pipeline implemented; real-data checkpoint not yet bundled.

## Purpose

`sar_spill_seg_v2` is the real-data semantic-segmentation path for Varuna-Netra. It is designed to identify oil-spill candidate pixels from dual-polarization Sentinel-1 SAR imagery while preserving analyst review and downstream explainable vessel attribution.

## Architecture

- Model: compact U-Net
- Inputs: 2 channels (`VV`, `VH`)
- Source representation: Sentinel-1 Sigma0 backscatter in dB
- Output: one oil-candidate probability mask
- Default training tile: 512 x 512
- Loss: 50% binary cross-entropy + 50% Dice loss
- Optimizer: AdamW

## Data

Training/validation sources:

- `10.5281/zenodo.8346860` — oil-spill scenes + masks
- `10.5281/zenodo.8253899` — no-oil and look-alike scenes

Independent test source:

- `10.5281/zenodo.13761290` — oil, no-oil and look-alike scenes

The training pipeline performs a stratified split by **complete source scene before tiles are generated**. This avoids the pixel/tile leakage possible when patches from one original scene appear in both training and validation.

## Preprocessing

Both VV and VH are read from the original TIFF. Non-finite values are sanitized. Sigma0 dB values are clipped to a configured fixed range (default `-50 dB` to `+5 dB`) and linearly scaled. The same transform must be used during training and inference.

The preprocessing range is stored in the checkpoint metadata rather than being treated as an undocumented constant.

## Evaluation

Validation metrics:

- Dice
- IoU
- precision
- recall
- F1

The untouched Part III test procedure additionally reports false-positive scene rates for:

- no-oil scenes
- oil-look-alike scenes

Part III must not be used to tune model weights or thresholds if its results are described as independent test performance.

## Limitations

- No field-performance numbers may be quoted until a real checkpoint has been trained and Part III evaluation has been completed.
- SAR oil-spill segmentation remains susceptible to low-wind zones, biogenic/natural films, wakes, coast contamination and other look-alikes.
- A segmentation output is evidence for analyst review, not proof of pollution source or legal responsibility.
- The model does not estimate oil volume from SAR alone.

## Deployment policy

The existing `sar_spill_pixel_v1` remains the default offline fallback until `sar_spill_seg_v2` has a validated checkpoint and an inference adapter is enabled. This prevents an untrained deep model from silently replacing the reproducible demo path.
