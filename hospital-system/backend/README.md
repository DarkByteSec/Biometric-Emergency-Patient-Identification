# Hospital backend

Flask app factory and local API. Run from this directory after installing `requirements.txt`:

```bash
export HOSPITAL_FLASK_SECRET="long-random-server-value"
export HOSPITAL_DATABASE_URL="sqlite:////absolute/path/hospital.sqlite3"
export CENTRAL_API_BASE_URL="https://central.example.invalid"
export HOSPITAL_CREDENTIAL_KEY_ID="server-only-key-id"
export HOSPITAL_CREDENTIAL_SECRET="server-only-secret"
python cli.py bootstrap-operator --role admin
python run.py
```

Use `PYTHONPATH=. pytest -q` for isolated SQLite tests. Bootstrap is interactive and refuses to create a second initial user.

## Local recognition workflow

Install `opencv-contrib-python-headless` (plus its `numpy` dependency) from
`requirements.txt`. The Hospital recognition flow is deliberately local-only:

1. An authenticated **admin** uploads an authorized synthetic JPEG/PNG sample
   at `POST /api/v1/recognition/enrollments`, with a Central citizen UUID. The
   Haar cascade must detect exactly one face. The stored file is a normalized
   face crop with a UUID-derived filename under a private directory; only
   metadata is written to `local_patient_subjects` and
   `local_biometric_samples`.
2. An admin calls `POST /api/v1/recognition/retrain`. Accepted local samples are
   read and an LBPH model is atomically saved with restrictive permissions.
3. An operator or admin posts a webcam frame to
   `POST /api/v1/recognition/probes`. The frame is bounded, decoded and
   discarded in memory. It is never persisted and is never sent to Central.
   Exactly one detected face is compared with the local model.
4. A `candidate` response contains a local subject ID and **always requires
   explicit operator confirmation** before the existing confirmation route can
   retrieve an allowlisted Central record. Rejecting a candidate never calls
   Central.

Safe outcomes include `no_face`, `multiple_faces`, `low_quality`, `no_match`,
and `unavailable` (for missing OpenCV-contrib, cascade, model, or enrollment
samples). These are not identity claims. LBPH's configurable distance threshold
is an **educational candidate threshold only**; it is not identity proof and
must not be used as a clinical or security decision. Configure deployment paths
and threshold through the parent application's config integration or these
environment variables: `HOSPITAL_RECOGNITION_SAMPLE_DIR`,
`HOSPITAL_RECOGNITION_MODEL_PATH`, `HOSPITAL_RECOGNITION_DISTANCE_THRESHOLD`,
and optionally `HOSPITAL_RECOGNITION_CASCADE_PATH`.

The admin UI at `/admin/recognition` provides the same enrollment and retrain
actions. Use synthetic/authorized test data only; no default accounts or sample
images are provided.
