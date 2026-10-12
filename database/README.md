# Database migrations and runtime roles

## Applied development state

The initial versioned migrations have been applied once to two separate, dedicated Supabase development projects. Production was explicitly excluded.

- Central: `central/migrations/0001_initial.sql` → project `BEPI Central Dev` (`sdkwktvfpuiijysigxlw`), migration version `20261012090928`.
- Hospital: `hospital/migrations/0001_initial.sql` → project `Hospital Dev` (`fwsdhwpjatltxlcqodfs`), migration version `20261012091049`.
- Each project was empty before migration; all expected tables, constraints, indexes, grants, RLS settings, and runtime policies were checked after application. Application tables had no rows after the migrations.
- The separate hosted verification record is [`SUPABASE_DEV_VERIFICATION.md`](SUPABASE_DEV_VERIFICATION.md).

The same SQL migrations were executed against separate disposable local PostgreSQL 16 databases. Both Flask PostgreSQL adapters subsequently passed smoke tests using non-superuser logins, TLS, and their respective `SET ROLE central_app` / `SET ROLE hospital_app` runtime boundaries. Local test fixtures were not uploaded to Supabase.

## Migration files and policy checks

- Central: `central/migrations/0001_initial.sql`
- Hospital: `hospital/migrations/0001_initial.sql`
- Central grant/RLS checks: `central/tests/0001_rls_and_grants.sql`
- Hospital grant/RLS checks: `hospital/tests/0001_rls_and_grants.sql`

Each migration belongs to its own project. The Hospital schema is not colocated with the Central schema; a separately deployed Hospital repeats its reviewed migration only in that Hospital's database.

For a future schema change: create a new sequential migration, review it, test it against a disposable PostgreSQL database, verify grants/RLS and constraints, then apply only to the explicitly selected development target. Check the migration history before retrying any operation whose response was interrupted. Do not edit or silently reapply an already-recorded migration.

## Runtime database roles

Each migration defines a non-login group role (`central_app` or `hospital_app`) without superuser, database-creation, role-creation, or RLS-bypass privileges. Application table access is granted to that role, and the Flask adapters assume it with `SET ROLE`.

The hosted projects do **not** yet have application runtime login roles or connection URLs provisioned. Before any hosted app connection, create a distinct, non-superuser runtime login for each project, grant only its matching group role, require TLS, and place the URL in an approved server-side secret store. Do not use a Supabase project-owner/postgres credential in either web application. Do not put database passwords in this repository, shell history, browser bundle, or chat.

## RLS and grants

All 17 application tables have RLS enabled. The `anon` and `authenticated` roles have no application-table privileges, and there are no explicit `PUBLIC` table grants. Each trusted non-login app role has the reviewed policies/grants needed by its Flask backend. Append-oriented access/audit tables intentionally do not grant runtime `UPDATE` or `DELETE`; session tables are mutable so revocation and expiry can be enforced.

The runtime policies are not a substitute for Flask authentication, Hospital credential scopes, field allowlists, CSRF checks, input validation, or audit logging. Any future table or view must receive reviewed privileges and RLS policies in the same migration; do not re-grant client access by default.

## Data, test fixtures, and secrets

The hosted migration left application tables empty. Do not add actual patient data or face images. The application encrypts National IDs and uses a separate keyed HMAC for exact matching; those keys are provisioned outside SQL and must be stored/backed up separately. Camera probes are not stored. Only explicitly authorized synthetic/test enrollment crops may be held in a protected Hospital-local directory.

## Rollback and recovery

The initial migration is additive and has no automatic destructive down migration. Reset only disposable local databases during tests. Before any hosted destructive operation, take and verify a backup and obtain the applicable approval. Restore tests, backup policies, retention settings, key rotation, monitoring/rate limiting, and production deployment controls remain separate work and have not been completed.
