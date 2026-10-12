# Biometric Emergency Patient Identification System

A university prototype for identifying a possible patient record in an emergency workflow. **Use synthetic data and explicitly authorized test images only.** This repository is not a clinical system and must not be used to make diagnosis, treatment, transfusion or autonomous identity decisions.

## Current status

- Approved planning document: [`PROJECT_MASTER_PLAN.md`](PROJECT_MASTER_PLAN.md)
- Repository scaffold and separate Central/Hospital database migration drafts are being prepared.
- SQL migration files have **not** been applied to Supabase or any other database.
- No application code, service credentials, real patient data, biometric images, cloud projects or deployments are included.
- The existing GitHub repository is public. Work from this session is being prepared locally and has not been pushed.

## Architecture

The proposed system has two independently deployable React + Flask applications. The Central System manages the synthetic patient registry and exposes a restricted HTTPS API. Each Hospital System performs face processing locally and connects to Central only through that API. Each Hospital installation has its own database and machine credential. Neither a browser nor a hospital connects directly to the Central database.

## Repository layout

```text
database/
├── central/
│   ├── migrations/   # Central Postgres migrations
│   ├── tests/        # pgTAP grant/RLS checks
│   └── seed/         # Synthetic fixtures, to be added in a later phase
└── hospital/
    ├── migrations/   # One hospital installation's local schema
    ├── tests/        # pgTAP grant/RLS checks
    └── seed/         # Synthetic fixtures, to be added in a later phase
docs/database/         # Database setup and migration notes
```

The application source directories will be added in later implementation phases after the approved milestones.

## Database migration status

The current drafts are:

- `database/central/migrations/0001_initial.sql`
- `database/hospital/migrations/0001_initial.sql`

They define only the schema, constraints, indexes, restricted runtime-role grants and RLS policies. See [`database/README.md`](database/README.md) for the intended review and application procedure. Do not run them against a live project until the target Central and Hospital projects, database roles and access permissions have been verified and application is explicitly authorized.

## Safety and security

- Synthetic or otherwise explicitly authorized test data only.
- Hospital credentials, database URLs, session keys and ID encryption keys must stay in server-side secret storage and out of Git and browser bundles.
- Face recognition produces a candidate only. Staff verification and manual lookup remain available.
- Never treat recorded blood type as a transfusion instruction; laboratory typing and crossmatching are required.
- Do not use Flask's development server as a public production service.

## Next steps

Review the migration drafts and role setup in `database/README.md`. The next application milestone is repository foundation and configuration; migrations are to be applied only after the target projects and permissions are confirmed.
