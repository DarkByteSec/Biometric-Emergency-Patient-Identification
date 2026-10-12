# Database migration workflow

## Status

The SQL files in this directory are **draft migrations**. They have not been executed against a local, hosted or Supabase database. Do not apply them until the project owner confirms the exact Central and Hospital project IDs, access permissions and approved migration operation.

- Central: `central/migrations/0001_initial.sql`
- Hospital: `hospital/migrations/0001_initial.sql`
- Central policy checks: `central/tests/0001_rls_and_grants.sql`
- Hospital policy checks: `hospital/tests/0001_rls_and_grants.sql`

Each migration belongs to a separate Supabase project. The Hospital migration is repeated only into that specific hospital installation's own database. Do not reuse the Central project for Hospital tables.

## Review before applying

1. Verify the target project identity and environment (development/test only for first application).
2. Confirm that the operator has authorization to run migrations and can create the required non-login database role.
3. Review the DDL, check constraints, identifier encryption/HMAC strategy, role grants, RLS policies and index choices against the approved plan.
4. Take a backup if applying to a non-disposable project. Never run these against production data as a test.
5. Apply one migration to the matching project, capture the result, then inspect tables, constraints, indexes, grants and RLS policies before continuing.
6. Run the pgTAP checks on a disposable Supabase-compatible test database with `pgtap` enabled. The test SQL assumes the standard Supabase `anon` and `authenticated` roles exist.

## Runtime database roles

Each migration creates a non-login group role (`central_app` or `hospital_app`) with no superuser, database-creation, role-creation or RLS-bypass privileges. Table grants are assigned to that role. A separate runtime login must be created with a generated secret through the approved database/secret-management process, then granted membership in the matching group role. The application connection must assume that role (for example with `SET ROLE central_app` or `SET ROLE hospital_app`) and must not use the project owner/postgres account.

Do not put runtime passwords in migrations, committed files, chat, shell history or CI logs. Migration/owner credentials must be separate from runtime credentials and absent from application containers/processes. The Flask backends are the only intended database clients; frontend bundles and hospital deployments receive no Central database credentials.

## RLS and grants

Both migrations remove client-facing grants on their application tables, enable RLS, and define runtime-role policies. The Flask API remains responsible for user and hospital authorization; the broad `USING (true)` policy for the trusted non-login runtime role does not replace Flask checks. The append-oriented access/audit tables intentionally do not grant runtime `UPDATE` or `DELETE`.

Supabase projects can have default privileges or later migration changes. Every future table or view must receive reviewed grants and RLS policies in the same migration. A future migration must not accidentally re-grant access to `anon`, `authenticated` or `PUBLIC`.

## Data and secrets

No seed records or biometrics are present in these migrations. Add only synthetic fixtures in a later approved phase. National ID encryption and keyed lookup HMAC are performed by the application; their keys are not SQL defaults and must be provisioned and backed up separately. Raw face probes are not stored. Local enrollment sample files belong in a protected Hospital data directory, not in Git.

## Rollback and recovery

The initial migration is additive and has no automatic destructive down migration. For local tests, reset only disposable databases. For hosted environments, take and verify a backup before any destructive rollback or schema rewrite. Test restore with synthetic data and record actual outcomes; do not claim that a backup or restore works until verified.
