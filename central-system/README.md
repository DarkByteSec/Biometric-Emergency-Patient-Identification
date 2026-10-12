# Central System

Synthetic/demo-safe Central registry and administrator console for the approved Biometric Emergency Patient Identification System. This is an educational prototype, not a clinical system or an identity authority.

## Local development

### Backend

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export APP_ENV=development
export CENTRAL_DATABASE_URL=sqlite:///central-demo.db
export CENTRAL_CORS_ORIGINS=http://localhost:5173
flask --app run.py bootstrap-admin   # interactive; no default password
python run.py                         # 127.0.0.1:5000
```

In a second terminal:

```bash
cd frontend
npm install
npm run dev                            # 127.0.0.1:5173
```

The Vite proxy sends `/api` requests to the local Flask backend. No database credential or Supabase client is included in the frontend.

## PostgreSQL and hosted development status

The Central schema migration was applied to the dedicated `BEPI Central Dev` Supabase project (`sdkwktvfpuiijysigxlw`) and verified. The separate production project was excluded. The Flask adapter uses TLS PostgreSQL URLs, refuses superuser logins, assumes the non-login `central_app` role, and checks that all expected migrated tables exist. It does not run schema-creation DDL against PostgreSQL.

No hosted Central runtime login or application connection URL has been provisioned. For future hosted use, provide a separate non-superuser login granted only `central_app`, store the TLS URL server-side, and configure secrets. Do not use a Supabase project-owner credential. The available `SUPABASE_URL` environment variable points at Production and must not be used by this app.

Required production values:

- `APP_ENV=production`
- `CENTRAL_DATABASE_URL=postgresql://...` with TLS required
- `CENTRAL_SESSION_SIGNING_KEY` with at least 32 characters
- `CENTRAL_ENCRYPTION_KEY` and `CENTRAL_LOOKUP_HMAC_KEY`, each encoding 32 bytes in base64url or hex
- `CENTRAL_CORS_ORIGINS` set to the exact trusted frontend origin(s)

Missing keys or a non-PostgreSQL production database fail closed. No `.env` file, credential, ID key, session key, plaintext password, or patient data belongs in Git.

## Capabilities and authorization

- Named admin/auditor accounts, HttpOnly sessions, double-submit CSRF for browser mutations, password hashing, security headers, and no public signup.
- National IDs are AES-GCM encrypted; exact lookup uses a distinct keyed HMAC. Access logs store only an opaque keyed fingerprint and limited suffix, never the raw identifier.
- Hospital credentials use a key ID plus a one-time-displayed secret. Stored verifier only; requests are bound to the centrally registered Hospital and scoped to a server-side allowlist.
- `patient.lookup` is required for Hospital record lookup. `health.read` is opt-in and gates blood type, allergy, and limited history fields. `patient.enroll` is opt-in and permits verification that a Central UUID exists before a local Hospital admin enrolls an authorized synthetic/test image. Central receives the UUID only, never image pixels.
- Lookup results are field-allowlisted and events are logged; a lookup or successful UUID check does not establish identity.

## Tests

```bash
cd backend
PYTHONPATH=. pytest -q
ruff check app tests
cd ../frontend
npm run build
```

Current verification: Central **7 backend tests pass**, lint passes, and the frontend production build passes. The PostgreSQL 16 adapter was exercised end-to-end against a disposable local database, including login, encrypted record creation, scoped credential issuance, lookup, enrollment verification, not-found event logging, and access-event reads. That test data was local-only and not uploaded to Supabase.

## Remaining production work

No hosted app login, seed account, deployment, monitoring/rate limiting, backup/restore test, or key-rotation process exists. Review the root README and `database/README.md` before configuring an environment. Do not use a Flask development server as a public service.
