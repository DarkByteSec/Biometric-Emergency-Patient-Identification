# Supabase Development Migration Verification — 2026-10-12

Source: authenticated Supabase MCP operations during task `KIoUqgtlte4UniRZ7FDl9q`.
No passwords, API keys, database URLs, or other credentials are recorded here.

## Projects

- Central Dev: `BEPI Central Dev`, ref `sdkwktvfpuiijysigxlw`, organization `sudpawlblqdejsihzbvt`, region `eu-west-3` (`eu-west-3` = EU West Paris), status `ACTIVE_HEALTHY`.
- Hospital Dev: `Hospital Dev`, ref `fwsdhwpjatltxlcqodfs`, same organization and region, status `ACTIVE_HEALTHY`, created after user approved organization and $0/month cost confirmation.
- Production ref `nbmxjyswsrffsslwrdwf` was explicitly excluded.

## Migrations

- Central migration `central_0001_initial`, recorded version `20261012090928`; `database/central/migrations/0001_initial.sql` applied to Central Dev.
- Hospital migration `hospital_0001_initial`, recorded version `20261012091049`; `database/hospital/migrations/0001_initial.sql` applied to Hospital Dev.
- The Central apply call was interrupted before its response, so status was rechecked: migration history showed it was applied once and all 10 tables existed.
- Both projects were empty before migration and have 0 rows in each application table afterward.

## Schema verification

- Central: 10 expected application tables, all with RLS enabled, one `central_app` policy each, columns/checks/PK/FK introspected, expected indexes present (2–6 per table).
- Hospital: 7 expected application tables, all with RLS enabled, one `hospital_app` policy each, columns/checks/PK/FK introspected, expected indexes present (2–6 per table).
- `anon` and `authenticated` have no SELECT/INSERT privileges on these application tables in either project.
- No explicit `PUBLIC` table privileges found on any of the 17 application tables.
- `central_app` and `hospital_app` are `NOLOGIN`, non-superuser, cannot create roles/databases, and do not bypass RLS.
- Central `patient_access_events` and `security_audit_events` and Hospital `local_scan_events` and `local_operational_events` have only SELECT/INSERT runtime grants (no UPDATE/DELETE). Session tables are intentionally mutable/deletable by the runtime role.

## Application follow-up

- Runtime login roles/connection URLs have not been provisioned; do not use project-owner credentials in app servers.
- Central and Hospital Flask applications are implemented and use their respective versioned schemas through non-superuser PostgreSQL adapters that assume the matching non-login runtime role. Adapter smoke tests passed against disposable local PostgreSQL databases; no hosted runtime login, hosted app authentication, or seed data has been provisioned or tested.
- The existing sandbox `SUPABASE_URL` points at Production and must not be used for application runtime.
