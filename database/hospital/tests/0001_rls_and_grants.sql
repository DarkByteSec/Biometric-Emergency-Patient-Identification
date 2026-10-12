-- Run with pgTAP in a disposable Supabase-compatible test database after migration 0001.
BEGIN;
SELECT plan(5);

SELECT ok(
  (SELECT count(*) = 7 AND bool_and(c.relrowsecurity)
   FROM pg_class AS c
   JOIN pg_namespace AS n ON n.oid = c.relnamespace
   WHERE n.nspname = 'public'
     AND c.relkind = 'r'
     AND c.relname IN (
       'hospital_profile', 'hospital_users', 'hospital_sessions',
       'local_patient_subjects', 'local_biometric_samples',
       'local_scan_events', 'local_operational_events'
     )),
  'RLS is enabled on every Hospital application table'
);

SELECT ok(
  NOT EXISTS (
    SELECT 1 FROM information_schema.role_table_grants
    WHERE table_schema = 'public'
      AND table_name IN (
        'hospital_profile', 'hospital_users', 'hospital_sessions',
        'local_patient_subjects', 'local_biometric_samples',
        'local_scan_events', 'local_operational_events'
      )
      AND grantee IN ('anon', 'authenticated', 'PUBLIC')
  ),
  'public client roles have no table grants'
);

SELECT ok(
  has_table_privilege('hospital_app', 'public.local_patient_subjects', 'SELECT')
  AND has_table_privilege('hospital_app', 'public.local_biometric_samples', 'INSERT'),
  'restricted Hospital runtime role has expected enrollment operations'
);

SELECT ok(
  has_table_privilege('hospital_app', 'public.local_scan_events', 'INSERT')
  AND NOT has_table_privilege('hospital_app', 'public.local_scan_events', 'UPDATE')
  AND NOT has_table_privilege('hospital_app', 'public.local_scan_events', 'DELETE'),
  'local scan events are append-only to the runtime role'
);

SELECT ok(
  has_table_privilege('hospital_app', 'public.local_operational_events', 'INSERT')
  AND NOT has_table_privilege('hospital_app', 'public.local_operational_events', 'UPDATE')
  AND NOT has_table_privilege('hospital_app', 'public.local_operational_events', 'DELETE'),
  'local operational events are append-only to the runtime role'
);

SELECT * FROM finish();
ROLLBACK;
