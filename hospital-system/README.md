# Hospital System

A separately deployable React + Flask application for one Hospital installation. Use synthetic records and explicitly authorized synthetic/test images only. This is an educational prototype, not a clinical system and not an identity authority.

## Local development

### Backend

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export APP_ENV=development
export HOSPITAL_DATABASE_URL=sqlite:///hospital-demo.db
export CENTRAL_API_BASE_URL=http://127.0.0.1:5000
python cli.py bootstrap-operator --role admin  # prompts for named email/password
python run.py                                  # 127.0.0.1:5001
```

Bootstrap creates only the first named account and refuses to run after a user exists. There are no default accounts or passwords.

### Frontend

```bash
cd frontend
npm install
npm run dev                                     # 127.0.0.1:5174
```

The browser calls only the same-origin Flask API through the Vite proxy. Hospital credentials belong only in the Hospital backend's protected environment/secret store. Never use a `VITE_` variable for a machine credential.

## Central connection

After bootstrapping the Central admin, register this Hospital and issue a one-time Central credential. Configure `HOSPITAL_CREDENTIAL_KEY_ID` and `HOSPITAL_CREDENTIAL_SECRET` on the Hospital backend only. Request the minimum scopes needed:

- `patient.lookup` for permitted synthetic/manual lookup;
- `health.read` only if the Hospital is permitted to see recorded blood type, allergies, and limited history;
- `patient.enroll` only if local enrollment requires Central to verify that a UUID exists.

Central lookups use outbound HTTPS with TLS verification, bounded connect/read timeouts, no automatic retry, and an allowlisted response. No image or biometric template is sent to Central. On outage, timeout, denial, or no match, the system preserves a distinct safe outcome and manual fallback; outage is never presented as `not_found`.

For a hosted runtime, use a separate non-superuser database login granted only `hospital_app`, require TLS, and store the connection URL and session secret server-side. The Hospital Dev schema exists and is verified in Supabase, but hosted runtime roles/URLs and app deployment have **not** been configured. Never use project-owner credentials, the session `SUPABASE_URL`, or the Central database connection in the Hospital app.

## Local face candidate workflow

The optional OpenCV-contrib LBPH workflow runs entirely within the Hospital backend:

1. A Hospital admin uploads an explicitly authorized synthetic/test sample and confirms authorization in the UI.
2. The backend validates the UUID and calls Central's `patient.enroll` endpoint. Central receives only the UUID; enrollment is denied if the scope is absent, the record is not found, or Central is unavailable.
3. Only after successful authorization does the backend locally detect one face, create a normalized crop, and store it in a private non-static directory. The raw upload is not sent to Central.
4. Admin retraining is local. Camera probe frames are processed in memory and are never persisted.
5. A possible LBPH match is a **candidate only**. A human must confirm or reject before any Central patient record is retrieved; it never establishes identity.

The backend requirements include `opencv-contrib-python-headless`; an isolated install was verified with OpenCV 4.14.0 and `cv2.face.LBPHFaceRecognizer_create`. If OpenCV, its cascade, the model, or samples are unavailable, recognition fails closed to an explicit `unavailable` outcome and manual lookup remains available.

## Tests and builds

```bash
cd backend
PYTHONPATH=. pytest -q
ruff check app
cd ../frontend
npm run build
```

Current verification: **16 Hospital backend tests pass**, application lint passes, and the production frontend build passes. Disposable local PostgreSQL 16 tests exercised readiness, login/session, append-only scan writes, JSONB operational events, and scan reads using TLS and a non-superuser runtime role. No local test row or sample was uploaded to Supabase.

## Production limitations

No hosted runtime login/URL, deployment, real data, monitoring/rate limiting, backup/restore test, key-rotation process, formal biometric performance validation, penetration test, or clinical/regulatory review has been completed. This code is not production-grade biometric identification. See the root README, backend README, and `database/README.md` for the present status and boundaries.
