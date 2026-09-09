# Judge demo script

**60 seconds:** open dashboard → show one spill candidate → show model/version/analyst-review flag → open correlation results → show the six scoring factors → open AIS and show `demo-replay` → trigger an alert and show it recorded through `demo-outbox`.

**Technical proof:** open `/api/config/defaults` and `/api/jobs` to show detector version, runtime worker count, and durable queue state.
