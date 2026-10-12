-- Run with pgTAP in a disposable Supabase-compatible test database after migration 0001.
BEGIN;
SELECT plan(5);

SELECT ok(
  (SELECT count(*) = 10 AND bool_and(c.relrowsecurity)
   FROM pg_class AS c
   JOIN pg_namespace AS n ON n.oid = c.relnamespace
   WHERE n.nspname = 'public'
     AND c.relkind = 'r'
     AND c.relname IN (
       'admin_users', 'admin_sessions', 'citizens', 'medical_history_entries',
       'patient_allergies', 'hospitals', 'hospital_credentials',
       'patient_access_events', 'biometric_registrations', 'security_audit_events'
     )),
  'RLS is enabled on every Central application table'
);

SELECT ok(
  NOT EXISTS (
    SELECT 1 FROM information_schema.role_table_grants
    WHERE table_schema = 'public'
      AND table_name IN (
        'admin_users', 'admin_sessions', 'citizens', 'medical_history_entries',
        'patient_allergies', 'hospitals', 'hospital_credentials',
        'patient_access_events', 'biometric_registrations', 'security_audit_events'
      )
      AND grantee IN ('anon', 'authenticated', 'PUBLIC')
  ),
  'public client roles have no table grants'
);

SELECT ok(
  has_table_privilege('central_app', 'public.citizens', 'SELECT')
  AND has_table_privilege('central_app', 'public.citizens', 'INSERT')
  AND has_table_privilege('central_app', 'public.citizens', 'UPDATE'),
  'restricted Central runtime role has expected citizen operations'
);

SELECT ok(
  has_table_privilege('central_app', 'public.patient_access_events', 'INSERT')
  AND NOT has_table_privilege('central_app', 'public.patient_access_events', 'UPDATE')
  AND NOT has_table_privilege('central_app', 'public.patient_access_events', 'DELETE'),
  'patient access events are append-only to the runtime role'
);

SELECT ok(
  has_table_privilege('central_app', 'public.security_audit_events', 'INSERT')
  AND NOT has_table_privilege('central_app', 'public.security_audit_events', 'UPDATE')
  AND NOT has_table_privilege('central_app', 'public.security_audit_events', 'DELETE'),
  'security audit events are append-only to the runtime role'
);

SELECT * FROM finish();
ROLLBACK;
