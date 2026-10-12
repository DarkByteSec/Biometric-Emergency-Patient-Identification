-- Hospital System schema, migration 0001.
-- Apply only to the approved Hospital Supabase project for this installation.
-- Each hospital installation must use a distinct database/project and synthetic data.
BEGIN;

CREATE EXTENSION IF NOT EXISTS citext;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hospital_app') THEN
    CREATE ROLE hospital_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
  END IF;
END
$$;

CREATE TABLE public.hospital_profile (
  id smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  hospital_name text NOT NULL CHECK (length(btrim(hospital_name)) BETWEEN 2 AND 150),
  central_hospital_id uuid NOT NULL UNIQUE,
  central_base_url text NOT NULL CHECK (length(central_base_url) BETWEEN 1 AND 2048),
  config_version integer NOT NULL DEFAULT 1 CHECK (config_version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.hospital_users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email citext NOT NULL UNIQUE,
  password_hash text NOT NULL,
  role text NOT NULL CHECK (role IN ('admin', 'operator')),
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  last_login_at timestamptz
);
CREATE INDEX hospital_users_active_role_idx ON public.hospital_users (is_active, role);

CREATE TABLE public.hospital_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  hospital_user_id uuid NOT NULL REFERENCES public.hospital_users(id) ON DELETE CASCADE,
  token_hash bytea NOT NULL UNIQUE,
  csrf_secret_hash bytea NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  last_seen_at timestamptz,
  revoked_at timestamptz,
  CHECK (expires_at > created_at),
  CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);
CREATE INDEX hospital_sessions_user_expiry_idx ON public.hospital_sessions (hospital_user_id, expires_at);
CREATE INDEX hospital_sessions_active_expiry_idx ON public.hospital_sessions (expires_at) WHERE revoked_at IS NULL;

CREATE TABLE public.local_patient_subjects (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  central_citizen_id uuid NOT NULL,
  recognizer_label integer NOT NULL UNIQUE CHECK (recognizer_label > 0),
  status text NOT NULL CHECK (status IN ('active', 'inactive', 'needs_review')),
  model_version text CHECK (model_version IS NULL OR length(model_version) <= 80),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX local_patient_subjects_central_id_idx ON public.local_patient_subjects (central_citizen_id);
CREATE INDEX local_patient_subjects_status_idx ON public.local_patient_subjects (status);

CREATE TABLE public.local_biometric_samples (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  subject_id uuid NOT NULL REFERENCES public.local_patient_subjects(id) ON DELETE CASCADE,
  file_key text NOT NULL UNIQUE CHECK (file_key ~ '^[0-9a-f-]{36}[.](jpg|png)$'),
  captured_at timestamptz NOT NULL DEFAULT now(),
  image_width integer NOT NULL CHECK (image_width BETWEEN 1 AND 10000),
  image_height integer NOT NULL CHECK (image_height BETWEEN 1 AND 10000),
  quality_status text NOT NULL CHECK (quality_status IN ('accepted', 'rejected', 'needs_review')),
  deleted_at timestamptz,
  CHECK (deleted_at IS NULL OR deleted_at >= captured_at)
);
CREATE INDEX local_biometric_samples_subject_date_idx ON public.local_biometric_samples (subject_id, captured_at DESC);

CREATE TABLE public.local_scan_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  hospital_user_id uuid REFERENCES public.hospital_users(id) ON DELETE SET NULL,
  started_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  method text NOT NULL CHECK (method IN ('face', 'national_id')),
  outcome text NOT NULL CHECK (outcome IN (
    'no_face', 'multiple_faces', 'low_quality', 'no_match', 'candidate',
    'verified', 'rejected', 'not_found', 'denied', 'unavailable', 'error'
  )),
  candidate_citizen_id uuid,
  score numeric CHECK (score IS NULL OR score >= 0),
  model_version text CHECK (model_version IS NULL OR length(model_version) <= 80),
  central_request_id uuid,
  central_status smallint CHECK (central_status IS NULL OR central_status BETWEEN 100 AND 599),
  error_code text CHECK (error_code IS NULL OR length(error_code) <= 80),
  CHECK (completed_at IS NULL OR completed_at >= started_at),
  CHECK (method <> 'national_id' OR (candidate_citizen_id IS NULL AND score IS NULL))
);
CREATE INDEX local_scan_events_time_idx ON public.local_scan_events (started_at DESC);
CREATE INDEX local_scan_events_outcome_time_idx ON public.local_scan_events (outcome, started_at DESC);
CREATE INDEX local_scan_events_operator_time_idx ON public.local_scan_events (hospital_user_id, started_at DESC);
CREATE INDEX local_scan_events_candidate_time_idx ON public.local_scan_events (candidate_citizen_id, started_at DESC) WHERE candidate_citizen_id IS NOT NULL;
CREATE INDEX local_scan_events_request_idx ON public.local_scan_events (central_request_id) WHERE central_request_id IS NOT NULL;

CREATE TABLE public.local_operational_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  occurred_at timestamptz NOT NULL DEFAULT now(),
  event_type text NOT NULL CHECK (length(event_type) BETWEEN 1 AND 100),
  outcome text NOT NULL CHECK (outcome IN ('success', 'failure', 'denied', 'suspicious')),
  request_id uuid,
  safe_details jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(safe_details) = 'object')
);
CREATE INDEX local_operational_events_time_idx ON public.local_operational_events (occurred_at DESC);
CREATE INDEX local_operational_events_type_time_idx ON public.local_operational_events (event_type, occurred_at DESC);

-- The Hospital database contains no Central data API credentials. The Central
-- machine credential belongs in server-only secret configuration, never here.
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

GRANT USAGE ON SCHEMA public TO hospital_app;
GRANT SELECT, INSERT, UPDATE ON public.hospital_profile TO hospital_app;
GRANT SELECT, INSERT, UPDATE ON public.hospital_users TO hospital_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.hospital_sessions TO hospital_app;
GRANT SELECT, INSERT, UPDATE ON public.local_patient_subjects TO hospital_app;
GRANT SELECT, INSERT, UPDATE ON public.local_biometric_samples TO hospital_app;
GRANT SELECT, INSERT ON public.local_scan_events TO hospital_app;
GRANT SELECT, INSERT ON public.local_operational_events TO hospital_app;

ALTER TABLE public.hospital_profile ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.hospital_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.hospital_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.local_patient_subjects ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.local_biometric_samples ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.local_scan_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.local_operational_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY hospital_app_profile ON public.hospital_profile FOR ALL TO hospital_app USING (true) WITH CHECK (true);
CREATE POLICY hospital_app_users ON public.hospital_users FOR ALL TO hospital_app USING (true) WITH CHECK (true);
CREATE POLICY hospital_app_sessions ON public.hospital_sessions FOR ALL TO hospital_app USING (true) WITH CHECK (true);
CREATE POLICY hospital_app_subjects ON public.local_patient_subjects FOR ALL TO hospital_app USING (true) WITH CHECK (true);
CREATE POLICY hospital_app_samples ON public.local_biometric_samples FOR ALL TO hospital_app USING (true) WITH CHECK (true);
CREATE POLICY hospital_app_scan_events ON public.local_scan_events FOR ALL TO hospital_app USING (true) WITH CHECK (true);
CREATE POLICY hospital_app_operational_events ON public.local_operational_events FOR ALL TO hospital_app USING (true) WITH CHECK (true);

COMMIT;
