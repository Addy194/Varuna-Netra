# Model card — sar_spill_pixel_v1

Type: 5-feature logistic pixel classifier. Features: darkness, local contrast, smoothness, gradient, coherence.

Training data: `synthetic_sar_demo_v1`, 180 deterministic synthetic chips, seed 194.

Validation: synthetic holdout only. The generated metrics are not evidence of field performance. Real Sentinel-1 labelled validation, geographic holdouts, look-alike analysis and threshold calibration are required before operational use.

Output: pixel-level oil candidate probability. The application intentionally calls this an **ML candidate**, not a confirmed oil spill.

Known look-alikes: low-wind areas, natural films/seep-like patterns, vessel wakes, coast/land contamination, rain/speckle artifacts.
