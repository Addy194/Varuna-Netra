# Varuna-Netra architecture

React UI → FastAPI API → MongoDB + durable Mongo job queue → detector/correlation/drift workers. External integrations (AISStream, Resend, object storage) are optional.

## Detector
1. Prefer Sentinel-1 VV/VH raw assets recorded from STAC.
2. Fall back to rendered preview when raw assets are unavailable.
3. Normalize SAR imagery and run the bundled pixel classifier.
4. Threshold/projection converts probabilities to candidate polygons.
5. Quality flags mark every candidate for analyst review.

## Queue
Jobs are persisted in MongoDB. Workers claim queued jobs atomically, use leases, and retry failures up to `JOB_MAX_ATTEMPTS`. A restart therefore does not erase the queue.
