# Real Sentinel-1 oil-spill training

Varuna-Netra keeps its small synthetic-trained classifier as an offline fallback, but the SIH real-data path is `sar_spill_seg_v2`: a two-channel VV/VH U-Net trained on labelled Sentinel-1 Sigma0 imagery.

## Fastest path

After creating the ML environment below, the complete workflow is one command:

```bash
python backend/training/run_real_sar_pipeline.py
```

It downloads the selected public Zenodo archives with resume support, verifies every archive against Zenodo's published MD5, extracts them, deletes the large compressed archives after successful extraction by default, discovers the actual extracted folders, trains the model, and evaluates the frozen best checkpoint on Part III.

If you want to download the data first and train later:

```bash
python backend/training/run_real_sar_pipeline.py --stage prepare
python backend/training/run_real_sar_pipeline.py --stage train
python backend/training/run_real_sar_pipeline.py --stage evaluate
```

The full source data are large. A machine with roughly **160-200+ GiB of free disk** is a safer target when archives are deleted after extraction. Keeping all compressed archives as well needs more space. A CUDA-capable NVIDIA GPU is strongly recommended for training, but dataset preparation itself does not require a GPU.

## Dataset

Use the three-part dataset by Trujillo-Acatitla et al. published on Zenodo:

- **Part I — oil spill train/validation scenes**
  - DOI: `10.5281/zenodo.8346860`
  - 1,200 Sentinel-1 oil-spill images and matching masks
- **Part II — negative/look-alike train/validation scenes**
  - DOI: `10.5281/zenodo.8253899`
  - 685 no-oil images and 685 look-alike images
- **Part III — independent test scenes**
  - DOI: `10.5281/zenodo.13761290`
  - 150 oil, 150 look-alike and 150 no-oil test images with ground truth

The published imagery is Sentinel-1 Sigma0 in dB, `2048 x 2048 x 2`, with VV and VH polarizations. Do not commit the archives, extracted TIFFs or trained checkpoints to Git; `.gitignore` excludes the data/checkpoint paths.

### Why Part II mask archives are skipped

For this binary oil-segmentation target, the dataset describes the no-oil and look-alike ground truth as zero. Varuna-Netra therefore creates zero masks for those two classes in memory and downloads only their SAR imagery. This avoids unnecessary files while still training the detector to reject difficult look-alikes.

## ML environment

Keep the PyTorch training stack separate from the production FastAPI environment.

Linux/macOS:

```bash
python -m venv .venv-ml
source .venv-ml/bin/activate
python -m pip install --upgrade pip
pip install -r backend/training/requirements-ml.txt
```

Windows PowerShell:

```powershell
python -m venv .venv-ml
.\.venv-ml\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r backend/training/requirements-ml.txt
```

For an NVIDIA GPU, install the CUDA-compatible PyTorch build recommended for that machine before running the pipeline. The training script automatically uses CUDA when PyTorch reports it available and enables mixed precision by default to reduce GPU memory use.

## Automated dataset preparation

The downloader is:

```bash
python backend/training/prepare_zenodo_data.py --data-root data/zenodo_sar
```

It performs:

1. resumable HTTP downloads;
2. verification against Zenodo-published MD5 checksums;
3. 7z extraction using an installed `7z` executable when available, with `py7zr` as the Python fallback;
4. per-archive preparation markers so completed archives are not downloaded again;
5. automatic deletion of verified archives after extraction unless `--keep-archives` is supplied;
6. a generated `dataset_manifest.json` recording source DOIs and extracted TIFF counts.

An interrupted `.part` download is resumed on the next run rather than restarted from zero when the server supports byte ranges.

## Training protocol

The one-command runner discovers the extracted directories automatically and calls `train_segmentation.py`. The important defaults are:

- two input channels: VV + VH;
- Sentinel-1 Sigma0 dB input, rather than rendered quicklook intensity;
- fixed dB scaling shared by training and inference;
- 512 x 512 tiles;
- oil-positive crop oversampling;
- new deterministic training crops every epoch;
- BCE + Dice loss;
- AdamW optimization;
- CUDA mixed precision when available;
- early stopping on validation Dice;
- stratified **source-scene split before tile generation**.

The scene-level split is critical. Pixels or tiles from the same source SAR image must never appear in both training and validation partitions.

The best model is written to:

```text
checkpoints/sar_spill_seg_v2.pt
```

Alongside it, training creates a JSON metadata sidecar and training-history JSON. The checkpoint records the dataset DOIs, train/validation scene counts, class counts, split unit, seed, preprocessing, model configuration, software versions, hyperparameters and best validation metrics.

## Independent Part III evaluation

Part III remains untouched until the best checkpoint is frozen. The runner then performs full-scene sliding-window inference and writes:

```text
checkpoints/sar_spill_seg_v2_test_metrics.json
```

The evaluator reports:

- Dice;
- Intersection over Union (IoU);
- precision;
- recall;
- F1;
- per-scene-class metrics;
- false-positive scene rate on no-oil scenes;
- false-positive scene rate on look-alike scenes.

Do not tune the model or probability threshold on Part III and then describe those results as independent test metrics. If threshold calibration is later added, use a validation-only calibration split and keep Part III sealed.

## Running on a smaller GPU

Start with a smaller batch rather than changing the scientific split:

```bash
python backend/training/run_real_sar_pipeline.py --stage train --batch-size 2
```

If necessary, use `--batch-size 1`. The 512 x 512 tile size is intentionally much smaller than the native 2048 x 2048 scenes.

## What to say at SIH

Until the checkpoint has actually been trained and independently evaluated, describe `sar_spill_seg_v2` as **the real-data training pipeline**, not as a validated deployed model.

After training, only quote numbers produced by `sar_spill_seg_v2_test_metrics.json`. Never copy expected, paper, or synthetic metrics and present them as Varuna-Netra results.

A defensible description is:

> Varuna-Netra uses dual-polarization Sentinel-1 VV/VH semantic segmentation. Training and validation are separated by complete source scenes, with an untouched independent test set containing oil, no-oil and look-alike cases. Vessel attribution remains a separate explainable decision-support stage and still requires analyst review.
