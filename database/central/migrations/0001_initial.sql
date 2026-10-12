-- Central System schema, migration 0001.
-- Apply only to the approved Central Supabase project after review.
-- This file is prepared, not applied. All patient fixtures must be synthetic.
BEGIN;

CREATE EXTENSION IF NOT EXISTS citext;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'central_app') THEN
    CREATE ROLE central_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
  END IF;
END
$$;

CREATE TABLE public.admin_users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email citext NOT NULL UNIQUE,
  password_hash text NOT NULL,
  role text NOT NULL CHECK (role IN ('admin', 'auditor')),
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  last_login_at timestamptz
);
CREATE INDEX admin_users_active_role_idx ON public.admin_users (is_active, role);

CREATE TABLE public.admin_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  admin_user_id uuid NOT NULL REFERENCES public.admin_users(id) ON DELETE CASCADE,
  token_hash bytea NOT NULL UNIQUE,
  csrf_secret_hash bytea NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  last_seen_at timestamptz,
  revoked_at timestamptz,
  CHECK (expires_at > created_at),
  CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);
CREATE INDEX admin_sessions_user_expiry_idx ON public.admin_sessions (admin_user_id, expires_at);
CREATE INDEX admin_sessions_active_expiry_idx ON public.admin_sessions (expires_at) WHERE revoked_at IS NULL;

CREATE TABLE public.citizens (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  national_id_ciphertext bytea NOT NULL,
  national_id_lookup_hmac bytea NOT NULL UNIQUE,
  national_id_mask_suffix varchar(4),
  first_name text NOT NULL CHECK (length(btrim(first_name)) BETWEEN 1 AND 100),
  last_name text NOT NULL CHECK (length(btrim(last_name)) BETWEEN 1 AND 100),
  date_of_birth date,
  gender text CHECK (gender IS NULL OR gender IN ('female', 'male', 'non_binary', 'other', 'undisclosed', 'unknown')),
  blood_type text NOT NULL DEFAULT 'unknown'
    CHECK (blood_type IN ('A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-', 'unknown')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  archived_at timestamptz,
  CHECK (national_id_mask_suffix IS NULL OR length(national_id_mask_suffix) <= 4),
  CHECK (archived_at IS NULL OR archived_at >= created_at)
);
CREATE INDEX citizens_name_search_idx ON public.citizens (lower(last_name), lower(first_name)) WHERE archived_at IS NULL;
CREATE INDEX citizens_dob_idx ON public.citizens (date_of_birth) WHERE archived_at IS NULL;

CREATE TABLE public.medical_history_entries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  citizen_id uuid NOT NULL REFERENCES public.citizens(id) ON DELETE RESTRICT,
  category text NOT NULL CHECK (category IN ('condition', 'medication', 'procedure', 'note')),
  summary text NOT NULL CHECK (length(btrim(summary)) BETWEEN 1 AND 2000),
  recorded_at timestamptz NOT NULL,
  source_label text CHECK (source_label IS NULL OR length(source_label) <= 160),
  is_active boolean NOT NULL DEFAULT true,
  created_by uuid REFERENCES public.admin_users(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX medical_history_citizen_date_idx ON public.medical_history_entries (citizen_id, is_active, recorded_at DESC);

CREATE TABLE public.patient_allergies (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  citizen_id uuid NOT NULL REFERENCES public.citizens(id) ON DELETE RESTRICT,
  substance text NOT NULL CHECK (length(btrim(substance)) BETWEEN 1 AND 240),
  reaction text CHECK (reaction IS NULL OR length(reaction) <= 1000),
  status text NOT NULL CHECK (status IN ('active', 'inactive', 'unknown')),
  recorded_at timestamptz NOT NULL,
  created_by uuid REFERENCES public.admin_users(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX patient_allergies_citizen_status_idx ON public.patient_allergies (citizen_id, status);

CREATE TABLE public.hospitals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code text NOT NULL UNIQUE CHECK (code ~ '^[A-Z0-9_-]{2,32}$'),
  name text NOT NULL CHECK (length(btrim(name)) BETWEEN 2 AND 150),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
  allowed_scopes text[] NOT NULL DEFAULT ARRAY['patient.lookup']::text[],
  contact_label text CHECK (contact_label IS NULL OR length(contact_label) <= 160),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz,
  CHECK (cardinality(allowed_scopes) > 0),
  CHECK (allowed_scopes <@ ARRAY['patient.lookup', 'patient.enroll', 'health.read']::text[])
);
CREATE INDEX hospitals_status_idx ON public.hospitals (status);
CREATE INDEX hospitals_last_seen_idx ON public.hospitals (last_seen_at DESC NULLS LAST);

CREATE TABLE public.hospital_credentials (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  hospital_id uuid NOT NULL REFERENCES public.hospitals(id) ON DELETE RESTRICT,
  key_id text NOT NULL UNIQUE CHECK (key_id ~ '^[A-Za-z0-9_-]{8,64}$'),
  secret_hash bytea NOT NULL,
  scopes text[] NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz,
  revoked_at timestamptz,
  created_by uuid REFERENCES public.admin_users(id) ON DELETE SET NULL,
  CHECK (cardinality(scopes) > 0),
  CHECK (scopes <@ ARRAY['patient.lookup', 'patient.enroll', 'health.read']::text[]),
  CHECK (expires_at IS NULL OR expires_at > created_at),
  CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);
CREATE INDEX hospital_credentials_site_status_idx ON public.hospital_credentials (hospital_id, revoked_at, expires_at);

CREATE TABLE public.patient_access_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id uuid NOT NULL,
  hospital_id uuid NOT NULL REFERENCES public.hospitals(id) ON DELETE RESTRICT,
  credential_id uuid REFERENCES public.hospital_credentials(id) ON DELETE SET NULL,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  operation text NOT NULL CHECK (operation IN ('patient_lookup', 'enrollment_verify')),
  outcome text NOT NULL CHECK (outcome IN ('success', 'not_found', 'denied', 'invalid_request', 'rate_limited', 'central_error')),
  http_status smallint CHECK (http_status BETWEEN 100 AND 599),
  citizen_id uuid REFERENCES public.citizens(id) ON DELETE SET NULL,
  identifier_fingerprint bytea,
  identifier_last2 varchar(2),
  duration_ms integer CHECK (duration_ms IS NULL OR duration_ms >= 0),
  error_code text CHECK (error_code IS NULL OR length(error_code) <= 80),
  recognition_method text CHECK (recognition_method IS NULL OR recognition_method IN ('manual_national_id', 'local_face_candidate')),
  model_version text CHECK (model_version IS NULL OR length(model_version) <= 80),
  CHECK (identifier_last2 IS NULL OR length(identifier_last2) <= 2)
);
CREATE INDEX patient_access_events_time_idx ON public.patient_access_events (occurred_at DESC);
CREATE INDEX patient_access_events_hospital_time_idx ON public.patient_access_events (hospital_id, occurred_at DESC);
CREATE INDEX patient_access_events_outcome_time_idx ON public.patient_access_events (outcome, occurred_at DESC);
CREATE INDEX patient_access_events_citizen_time_idx ON public.patient_access_events (citizen_id, occurred_at DESC) WHERE citizen_id IS NOT NULL;
CREATE INDEX patient_access_events_request_idx ON public.patient_access_events (request_id);

CREATE TABLE public.biometric_registrations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  hospital_id uuid NOT NULL REFERENCES public.hospitals(id) ON DELETE RESTRICT,
  citizen_id uuid NOT NULL REFERENCES public.citizens(id) ON DELETE RESTRICT,
  status text NOT NULL CHECK (status IN ('active', 'inactive', 'needs_review')),
  algorithm text NOT NULL CHECK (length(algorithm) BETWEEN 1 AND 80),
  model_version text NOT NULL CHECK (length(model_version) BETWEEN 1 AND 80),
  sample_count smallint NOT NULL DEFAULT 0 CHECK (sample_count BETWEEN 0 AND 50),
  registered_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  created_by_label text CHECK (created_by_label IS NULL OR length(created_by_label) <= 160),
  UNIQUE (hospital_id, citizen_id)
);
CREATE INDEX biometric_registrations_hospital_status_idx ON public.biometric_registrations (hospital_id, status);

CREATE TABLE public.security_audit_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  occurred_at timestamptz NOT NULL DEFAULT now(),
  actor_type text NOT NULL CHECK (actor_type IN ('admin', 'hospital', 'system')),
  actor_admin_id uuid REFERENCES public.admin_users(id) ON DELETE SET NULL,
  actor_hospital_id uuid REFERENCES public.hospitals(id) ON DELETE SET NULL,
  action text NOT NULL CHECK (length(action) BETWEEN 1 AND 100),
  target_type text CHECK (target_type IS NULL OR length(target_type) <= 80),
  target_id uuid,
  outcome text NOT NULL CHECK (outcome IN ('success', 'failure', 'denied', 'suspicious')),
  request_id uuid,
  details jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(details) = 'object'),
  CHECK (
    (actor_type = 'admin' AND actor_admin_id IS NOT NULL AND actor_hospital_id IS NULL) OR
    (actor_type = 'hospital' AND actor_hospital_id IS NOT NULL AND actor_admin_id IS NULL) OR
    (actor_type = 'system' AND actor_admin_id IS NULL AND actor_hospital_id IS NULL)
  )
);
CREATE INDEX security_audit_events_time_idx ON public.security_audit_events (occurred_at DESC);
CREATE INDEX security_audit_events_actor_time_idx ON public.security_audit_events (actor_type, occurred_at DESC);
CREATE INDEX security_audit_events_action_time_idx ON public.security_audit_events (action, occurred_at DESC);
CREATE INDEX security_audit_events_request_idx ON public.security_audit_events (request_id) WHERE request_id IS NOT NULL;

-- Remove default client access. RLS and grants are separate controls.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM authenticated';
  END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO central_app;
GRANT SELECT, INSERT, UPDATE ON public.admin_users TO central_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.admin_sessions TO central_app;
GRANT SELECT, INSERT, UPDATE ON public.citizens TO central_app;
GRANT SELECT, INSERT, UPDATE ON public.medical_history_entries TO central_app;
GRANT SELECT, INSERT, UPDATE ON public.patient_allergies TO central_app;
GRANT SELECT, INSERT, UPDATE ON public.hospitals TO central_app;
GRANT SELECT, INSERT, UPDATE ON public.hospital_credentials TO central_app;
GRANT SELECT, INSERT ON public.patient_access_events TO central_app;
GRANT SELECT, INSERT, UPDATE ON public.biometric_registrations TO central_app;
GRANT SELECT, INSERT ON public.security_audit_events TO central_app;

ALTER TABLE public.admin_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.admin_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.citizens ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.medical_history_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.patient_allergies ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.hospitals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.hospital_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.patient_access_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.biometric_registrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.security_audit_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY central_app_admin_users ON public.admin_users FOR ALL TO central_app USING (true) WITH CHECK (true);
CREATE POLICY central_app_admin_sessions ON public.admin_sessions FOR ALL TO central_app USING (true) WITH CHECK (true);
CREATE POLICY central_app_citizens ON public.citizens FOR ALL TO central_app USING (true) WITH CHECK (true);
CREATE POLICY central_app_medical_history ON public.medical_history_entries FOR ALL TO central_app USING (true) WITH CHECK (true);
CREATE POLICY central_app_patient_allergies ON public.patient_allergies FOR ALL TO central_app USING (true) WITH CHECK (true);
CREATE POLICY central_app_hospitals ON public.hospitals FOR ALL TO central_app USING (true) WITH CHECK (true);
CREATE POLICY central_app_hospital_credentials ON public.hospital_credentials FOR ALL TO central_app USING (true) WITH CHECK (true);
CREATE POLICY central_app_patient_access_events ON public.patient_access_events FOR ALL TO central_app USING (true) WITH CHECK (true);
CREATE POLICY central_app_biometric_registrations ON public.biometric_registrations FOR ALL TO central_app USING (true) WITH CHECK (true);
CREATE POLICY central_app_security_audit_events ON public.security_audit_events FOR ALL TO central_app USING (true) WITH CHECK (true);

COMMIT;
