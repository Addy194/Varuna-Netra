# Real Sentinel-1 oil-spill training

Varuna-Netra keeps its small synthetic-trained classifier as an offline fallback, but the SIH real-data path is `sar_spill_seg_v2`: a two-channel VV/VH U-Net trained on labelled Sentinel-1 Sigma0 imagery.

## Dataset

Use the three-part dataset by Trujillo-Acatitla et al. published on Zenodo:

- **Part I — oil spill train/validation scenes:** https://zenodo.org/records/8346860
  - DOI: `10.5281/zenodo.8346860`
  - 1,200 Sentinel-1 oil-spill images and matching masks
- **Part II — negative/look-alike train/validation scenes:** https://zenodo.org/records/8253899
  - DOI: `10.5281/zenodo.8253899`
  - 685 no-oil images and 685 look-alike images
- **Part III — independent test scenes:** https://zenodo.org/records/13761290
  - DOI: `10.5281/zenodo.13761290`
  - 150 oil, 150 look-alike and 150 no-oil test images with ground truth

The published imagery is Sentinel-1 Sigma0 in dB, `2048 x 2048 x 2`, with VV and VH polarizations. Do not commit the archives, extracted TIFFs or trained checkpoints to Git; `.gitignore` excludes `data/`, `checkpoints/`, `*.pt`, `*.pth`, and `*.ckpt`.

## Recommended local layout

The training commands do not require exact parent folder names, but this structure is convenient:

```text
data/
  zenodo_s1_oil/
    part1/
      01_Train_Val_Oil_Spill_images/
      01_Train_Val_Oil_Spill_mask/
    part2/
      01_Train_Val_No_Oil_Images/
      01_Train_Val_Lookalike_images/
    part3/
      oil_images/
      oil_masks/
      no_oil_images/
      lookalike_images/
checkpoints/
```

After extracting Part III, point the evaluation command to the actual corresponding directories; the archive's parent folder naming can vary.

## ML environment

Keep the PyTorch training stack separate from the production FastAPI environment:

```bash
python -m venv .venv-ml
source .venv-ml/bin/activate       # Linux/macOS
# .venv-ml\Scripts\activate        # Windows PowerShell
python -m pip install --upgrade pip
pip install -r backend/training/requirements-ml.txt
```

For a CUDA GPU, install the appropriate PyTorch build from the official PyTorch install selector, then install the remaining requirements.

## Train

```bash
python backend/training/train_segmentation.py \
  --oil-images data/zenodo_s1_oil/part1/01_Train_Val_Oil_Spill_images \
  --oil-masks data/zenodo_s1_oil/part1/01_Train_Val_Oil_Spill_mask \
  --no-oil-images data/zenodo_s1_oil/part2/01_Train_Val_No_Oil_Images \
  --lookalike-images data/zenodo_s1_oil/part2/01_Train_Val_Lookalike_images \
  --output checkpoints/sar_spill_seg_v2.pt
```

Default training uses:

- two input channels: VV + VH
- fixed Sigma0-dB scaling shared by training and inference
- 512 x 512 tiles
- oil-positive crop oversampling
- BCE + Dice loss
- AdamW optimization
- early stopping on validation Dice
- a stratified **source-scene split before tile generation**

The scene-level split is important: pixels or tiles from the same source SAR image must never appear in both train and validation partitions.

## Independent Part III evaluation

Part III is reserved for final evaluation. Do not tune model weights or the probability threshold on Part III and then describe its results as independent test metrics.

```bash
python backend/training/evaluate_segmentation.py \
  --checkpoint checkpoints/sar_spill_seg_v2.pt \
  --oil-images data/zenodo_s1_oil/part3/oil_images \
  --oil-masks data/zenodo_s1_oil/part3/oil_masks \
  --no-oil-images data/zenodo_s1_oil/part3/no_oil_images \
  --lookalike-images data/zenodo_s1_oil/part3/lookalike_images
```

The evaluator performs sliding-window full-scene inference and writes:

- Dice
- Intersection over Union (IoU)
- precision
- recall
- F1
- per-class metrics
- false-positive scene rate for no-oil scenes
- false-positive scene rate for look-alike scenes

Those are much more meaningful judge-facing metrics than synthetic pixel accuracy.

## What to say at SIH

Until the checkpoint has actually been trained and independently evaluated, describe `sar_spill_seg_v2` as **the real-data training pipeline**, not as a validated deployed model.

After training, only quote numbers produced by the saved evaluation JSON. Never copy expected, paper, or synthetic metrics and present them as Varuna-Netra results.

A defensible description is:

> Varuna-Netra uses dual-polarization Sentinel-1 VV/VH semantic segmentation. Training and validation are separated by complete source scenes, with an untouched independent test set containing oil, no-oil and look-alike cases. Vessel attribution remains a separate explainable decision-support stage and still requires analyst review.
