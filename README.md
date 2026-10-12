# Biometric Emergency Patient Identification System

A two-part React + Flask educational prototype for searching a possible patient record and, optionally, proposing a local face-match candidate. **Use synthetic records and explicitly authorized synthetic/test images only.** This is not a clinical system and must not be used to diagnose, prescribe, make transfusion decisions, or establish identity autonomously.

## Current status

- Both React + Flask applications and separate Central/Hospital PostgreSQL migrations are in the repository.
- The versioned migrations were applied to two dedicated Supabase **development** projects and independently verified. Production was excluded. See [Supabase development verification](docs/database/SUPABASE_DEV_VERIFICATION.md).
- The migrations also execute against disposable local PostgreSQL 16 databases. Both Flask database adapters passed real PostgreSQL/TLS smoke tests with non-superuser runtime logins.
- Central backend: **7 tests passed**; Hospital backend: **16 tests passed**. Both frontend production builds pass. Python application lint passes. Both local pgTAP suites pass all 5 assertions.
- No runtime login roles/URLs have been provisioned for the hosted Supabase projects, no test users or patient rows were seeded there, and no web application has been deployed. Do not connect apps using project-owner credentials. The session-level `SUPABASE_URL` is associated with Production and must not be used by either app.

## Architecture and safety boundary

- **Central System**: administrator console and API for a synthetic registry. National IDs are encrypted at rest and searched using a separate keyed HMAC. Hospital machine credentials are scoped and secrets are shown once.
- **Hospital System**: independent operator console, local scan/event store, and optional local OpenCV-contrib LBPH enrollment/probe workflow. Camera probes are handled in memory and are never sent to Central. Authorized enrollment requires a local admin confirmation and a Central UUID check using `patient.enroll`; only the UUID is sent to Central.
- A face result is a **candidate only**, never proof of identity. Manual lookup remains available. Recorded blood type/allergies may be missing or stale; laboratory typing, crossmatch, and clinical judgment remain mandatory.
- The browser calls only its same-origin Flask API. A Hospital backend talks to the Central API over HTTPS; it never connects to the Central database.

## Repository layout

```text
central-system/       Central React + Flask application
hospital-system/      Independent Hospital React + Flask application
 database/             Versioned Central and Hospital PostgreSQL migrations and pgTAP checks
docs/database/         Migration workflow and hosted verification record
PROJECT_MASTER_PLAN.md Approved project architecture and acceptance criteria
```

## Local development

Use separate terminals. Install each backend's `requirements.txt` and frontend's `package.json` dependencies. Use synthetic data only.

**Central backend** (from `central-system/backend`):

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export APP_ENV=development
export CENTRAL_DATABASE_URL=sqlite:///central-demo.db
export CENTRAL_CORS_ORIGINS=http://localhost:5173
flask --app run.py bootstrap-admin   # interactive; no default password
python run.py                         # 127.0.0.1:5000
```

**Central frontend** (from `central-system/frontend`):

```bash
npm install
npm run dev                            # 127.0.0.1:5173
```

**Hospital backend** (from `hospital-system/backend`):

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt        # includes OpenCV-contrib LBPH
export APP_ENV=development
export HOSPITAL_DATABASE_URL=sqlite:///hospital-demo.db
export CENTRAL_API_BASE_URL=http://127.0.0.1:5000
export HOSPITAL_CREDENTIAL_KEY_ID=server-side-value
export HOSPITAL_CREDENTIAL_SECRET=server-side-value
python cli.py bootstrap-operator --role admin  # prompts; no default password
python run.py                          # 127.0.0.1:5001
```

**Hospital frontend** (from `hospital-system/frontend`):

```bash
npm install
npm run dev                            # 127.0.0.1:5174
```

Local Central/Hospital credentials are configured on the **backend only**. Create and issue the Central Hospital credential through the Central admin UI after both local admin accounts have been bootstrapped. Enable only the scopes the Hospital needs (`health.read` and `patient.enroll` are opt-in). Never put the credential in a Vite environment variable or browser bundle.

## Hosted development databases

| System | Supabase project | Project ref | State |
|---|---|---|---|
| Central | BEPI Central Dev | `sdkwktvfpuiijysigxlw` | Migration applied and schema/RLS verified |
| Hospital | Hospital Dev | `fwsdhwpjatltxlcqodfs` | Migration applied and schema/RLS verified |

Both projects are in the approved organization and EU West (Paris). RLS is enabled for all 17 application tables; `anon`/`authenticated` have no table privileges; app group roles are `NOLOGIN` and cannot bypass RLS. Hosted application tables were empty after migration. A detailed record is in [`docs/database/SUPABASE_DEV_VERIFICATION.md`](docs/database/SUPABASE_DEV_VERIFICATION.md).

Before connecting a hosted application, provision a separate non-superuser runtime login for each project, grant only the corresponding `central_app` or `hospital_app` role, and store its TLS connection URL and application secrets in a server-side secret store. This has **not** been done. Do not use the Supabase project-owner account in a runtime app. Do not apply these migrations to Production.

## Validation

```bash
# Central backend
cd central-system/backend && PYTHONPATH=. pytest -q && ruff check app tests
# Central frontend
cd central-system/frontend && npm run build
# Hospital backend
cd hospital-system/backend && PYTHONPATH=. pytest -q && ruff check app
# Hospital frontend
cd hospital-system/frontend && npm run build
```

The tests cover authentication/CSRF, Central credential scopes and response minimization, enrollment verification, local-only image behavior, candidate confirmation/rejection, safe errors, and local operational logging. Disposable local PostgreSQL execution separately exercised both migrations, PostgreSQL app adapters, login, representative database writes, scoped Central lookup, and enrollment verification.

## Not yet production-ready

No production environment, runtime database login, monitoring/rate limiting, backup/restore test, key rotation process, deployment hardening, penetration test, formal biometric validation, or clinical/regulatory review is included. The OpenCV LBPH workflow is educational and must not be represented as production-grade biometric identification. See the project plan and `database/README.md` before expanding scope.
