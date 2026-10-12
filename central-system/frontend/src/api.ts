export type ApiError = { code: string; message: string };
export type Envelope<T> = {
  data: T | null;
  error: ApiError | null;
  request_id: string;
};

let csrf = '';
export const setCsrf = (value: string) => {
  csrf = value;
};

function cookieValue(name: string): string {
  const prefix = `${name}=`;
  const entry = document.cookie.split('; ').find((part) => part.startsWith(prefix));
  return entry ? decodeURIComponent(entry.slice(prefix.length)) : '';
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  if (!['GET', 'HEAD', 'OPTIONS'].includes((init.method || 'GET').toUpperCase())) {
    const token = csrf || cookieValue('central_csrf');
    if (token) headers.set('X-CSRF-Token', token);
  }
  const response = await fetch(path, { ...init, headers, credentials: 'include' });
  const body: Envelope<T> = await response.json().catch(() => ({
    data: null,
    error: { code: 'NETWORK', message: 'The service returned an unreadable response.' },
    request_id: '',
  }));
  if (response.status === 401) csrf = '';
  if (!response.ok || body.error) {
    throw body.error ?? { code: 'REQUEST_FAILED', message: 'Request failed.' };
  }
  return body.data as T;
}

export type Admin = { id: string; email: string; role: 'admin' | 'auditor' };
export type Citizen = {
  id: string;
  first_name: string;
  last_name: string;
  date_of_birth: string | null;
  gender: string | null;
  blood_type: string;
  national_id_masked: string | null;
  created_at: string;
  updated_at: string;
  history?: History[];
  allergies?: Allergy[];
};
export type History = {
  id: string;
  category: string;
  summary: string;
  recorded_at: string;
  source_label: string | null;
  is_active: boolean;
};
export type Allergy = {
  id: string;
  substance: string;
  reaction: string | null;
  status: string;
  recorded_at: string;
};
export type Hospital = {
  id: string;
  code: string;
  name: string;
  status: string;
  allowed_scopes: string[];
  contact_label: string | null;
  last_seen_at: string | null;
};

export const api = {
  login: async (email: string, password: string) => {
    const data = await request<{ admin: Admin; csrf_token: string }>(
      '/api/v1/admin/login',
      { method: 'POST', body: JSON.stringify({ email, password }) },
    );
    setCsrf(data.csrf_token);
    return data.admin;
  },
  logout: async () => {
    const result = await request('/api/v1/admin/logout', { method: 'POST' });
    setCsrf('');
    return result;
  },
  session: () => request<{ admin: Admin }>('/api/v1/admin/session'),
  dashboard: () => request<Record<string, number>>('/api/v1/admin/dashboard'),
  citizens: (q = '') =>
    request<{ items: Citizen[]; page: number; limit: number }>(
      `/api/v1/citizens?q=${encodeURIComponent(q)}`,
    ),
  citizen: (id: string) => request<Citizen>(`/api/v1/citizens/${id}`),
  createCitizen: (body: Record<string, unknown>) =>
    request<Citizen>('/api/v1/citizens', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  updateCitizen: (id: string, body: Record<string, unknown>) =>
    request<Citizen>(`/api/v1/citizens/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  hospitals: () => request<{ items: Hospital[] }>('/api/v1/hospitals'),
  createHospital: (body: Record<string, unknown>) =>
    request<Hospital>('/api/v1/hospitals', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  updateHospital: (id: string, body: Record<string, unknown>) =>
    request(`/api/v1/hospitals/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
  issueCredential: (id: string, scopes: string[] = ['patient.lookup']) =>
    request<{ credential: string; key_id: string; secret: string; scopes: string[] }>(
      `/api/v1/hospitals/${id}/credentials`,
      { method: 'POST', body: JSON.stringify({ scopes }) },
    ),
  revokeCredential: (hospitalId: string, credentialId: string) =>
    request(`/api/v1/hospitals/${hospitalId}/credentials/${credentialId}`, {
      method: 'POST',
      body: JSON.stringify({ action: 'revoke' }),
    }),
  accessEvents: () => request<{ items: Record<string, unknown>[] }>('/api/v1/access-events'),
  securityEvents: () => request<{ items: Record<string, unknown>[] }>('/api/v1/security-events'),
};
