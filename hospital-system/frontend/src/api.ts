export type Outcome =
  | 'no_face'
  | 'multiple_faces'
  | 'low_quality'
  | 'no_match'
  | 'candidate'
  | 'verified'
  | 'rejected'
  | 'not_found'
  | 'denied'
  | 'unavailable'
  | 'timeout'
  | 'error'
  | 'accepted'
  | 'trained';

export type Allergy = {
  name?: string;
  reaction?: string | null;
  status?: string;
  recorded_at?: string;
};

export type Patient = {
  citizen_id?: string;
  display_name?: string;
  date_of_birth?: string | null;
  blood_type?: string | null;
  allergies?: Allergy[] | null;
  limited_history?: Array<{ label?: string; recorded_at?: string }> | null;
  recorded_at?: string;
  record_status?: string;
};

export type Scan = {
  id: string;
  started_at: string;
  completed_at?: string;
  method: string;
  outcome: Outcome;
  score?: number | null;
  model_version?: string | null;
  central_status?: number | null;
  error_code?: string | null;
  candidate_present: boolean;
};

let csrf = '';

function cookieValue(name: string): string {
  const prefix = `${name}=`;
  const entry = document.cookie.split('; ').find((part) => part.startsWith(prefix));
  return entry ? decodeURIComponent(entry.slice(prefix.length)) : '';
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const method = (init.method || 'GET').toUpperCase();
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const token = csrf || cookieValue('hospital_csrf');
    if (token) headers.set('X-CSRF-Token', token);
  }
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers,
    credentials: 'include',
  });
  const body = await response.json().catch(() => ({}));
  if (response.status === 401) csrf = '';
  if (!response.ok) {
    const error = body.error || {};
    throw Object.assign(new Error(error.message || 'Request failed'), {
      code: error.code,
      status: response.status,
      requestId: body.request_id,
    });
  }
  return body.data as T;
}

export const api = {
  login: async (email: string, password: string) => {
    const result = await request<{
      user: { id: string; email: string; role: string };
      csrf_token: string;
    }>('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) });
    csrf = result.csrf_token;
    return result;
  },
  session: () => request<{ user: { id?: string; email: string; role: string } }>('/auth/session'),
  logout: async () => {
    const result = await request('/auth/logout', { method: 'POST' });
    csrf = '';
    return result;
  },
  lookup: (national_id: string) =>
    request<Patient>('/patient-lookups', {
      method: 'POST',
      body: JSON.stringify({ national_id }),
    }),
  probe: (blob: Blob) => {
    const form = new FormData();
    form.append('frame', blob, 'probe.jpg');
    return request<{
      outcome: Outcome;
      scan_id: string;
      candidate_id?: string;
      operator_confirmation_required?: boolean;
      reason_code?: string;
    }>('/recognition/probes', { method: 'POST', body: form });
  },
  confirm: (id: string, decision: 'confirm' | 'reject') =>
    request<{ outcome: Outcome; patient?: Patient }>(
      `/patient-lookups/${encodeURIComponent(id)}/confirm`,
      { method: 'POST', body: JSON.stringify({ decision }) },
    ),
  scans: () => request<{ items: Scan[] }>('/scans'),
  enroll: (
    central_citizen_id: string,
    file: File,
    authorization_confirmed: boolean,
    subject_id?: string,
  ) => {
    const form = new FormData();
    form.append('central_citizen_id', central_citizen_id);
    form.append('authorization_confirmed', String(authorization_confirmed));
    if (subject_id) form.append('subject_id', subject_id);
    form.append('sample', file, file.name);
    return request<{
      outcome: Outcome;
      sample_id?: string;
      subject_id?: string;
      reason_code?: string;
    }>('/recognition/enrollments', { method: 'POST', body: form });
  },
  retrain: () =>
    request<{
      outcome: Outcome;
      model_version?: string;
      sample_count?: number;
      subject_count?: number;
      reason_code?: string;
    }>('/recognition/retrain', { method: 'POST' }),
};
