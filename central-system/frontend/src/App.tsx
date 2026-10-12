import { FormEvent, useEffect, useState } from 'react';
import {
  BrowserRouter,
  Link,
  Route,
  Routes,
  useNavigate,
  useParams,
} from 'react-router-dom';
import { api, Admin, Citizen, Hospital } from './api';
import './styles.css';

const Banner = () => (
  <div className="banner">
    <strong>Synthetic-only prototype.</strong> This console is not a clinical system.
    Recorded blood type is not a transfusion instruction; verify with laboratory
    typing and crossmatch. <b>Unknown allergies are not absence of allergies.</b>
  </div>
);

function Login({ onLogin }: { onLogin: (admin: Admin) => void }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      onLogin(await api.login(email, password));
    } catch (caught: any) {
      setError(caught.message || 'Unable to sign in.');
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="login">
      <section className="card">
        <p className="eyebrow">CENTRAL OPERATIONS</p>
        <h1>Emergency patient registry</h1>
        <p className="muted">Authorized administrator access only</p>
        <Banner />
        <form onSubmit={submit}>
          <label>
            Email
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
              autoComplete="username"
            />
          </label>
          <label>
            Password
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
              autoComplete="current-password"
            />
          </label>
          {error && <div className="error" role="alert">{error}</div>}
          <button disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
        </form>
      </section>
    </main>
  );
}

function Shell({ admin, onLogout }: { admin: Admin; onLogout: () => void }) {
  return (
    <>
      <header>
        <div>
          <span className="eyebrow">CENTRAL OPERATIONS</span>
          <strong>Emergency patient registry</strong>
        </div>
        <nav>
          <Link to="/">Overview</Link>
          <Link to="/citizens">Citizens</Link>
          <Link to="/hospitals">Hospitals</Link>
          <Link to="/logs">Logs</Link>
          <span className="role">{admin.role}</span>
          <button className="linkbtn" onClick={onLogout}>Sign out</button>
        </nav>
      </header>
      <Banner />
      <main className="page">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/citizens" element={<Citizens />} />
          <Route path="/citizens/:id" element={<CitizenDetail />} />
          <Route path="/hospitals" element={<Hospitals />} />
          <Route path="/logs" element={<Logs />} />
        </Routes>
      </main>
    </>
  );
}

function Dashboard() {
  const [data, setData] = useState<Record<string, number> | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    api.dashboard().then(setData).catch((caught) => setError(caught.message));
  }, []);
  const metrics: Array<[string, string]> = [
    ['citizens', 'Active citizens'],
    ['hospitals', 'Active hospitals'],
    ['access_events', 'Patient access events'],
    ['security_events', 'Security audit events'],
  ];
  return (
    <>
      <h1>Overview</h1>
      {error && <div className="error">{error}</div>}
      <div className="grid">
        {metrics.map(([key, label]) => (
          <div className="metric" key={key}>
            <span>{label}</span>
            <b>{data ? data[key] : '—'}</b>
          </div>
        ))}
      </div>
      <section className="card">
        <h2>Operational safeguards</h2>
        <ul>
          <li>National IDs are encrypted and exact lookup uses a separate keyed fingerprint.</li>
          <li>Hospital identity is derived from its server credential, never a caller-supplied hospital ID.</li>
          <li>All lookups and denials are recorded without raw identifiers, credentials, or request bodies.</li>
        </ul>
      </section>
    </>
  );
}

function Citizens() {
  const [items, setItems] = useState<Citizen[]>([]);
  const [query, setQuery] = useState('');
  const [error, setError] = useState('');
  const load = () =>
    api.citizens(query).then((result) => setItems(result.items)).catch((caught) => setError(caught.message));
  useEffect(() => { void load(); }, []);
  return (
    <>
      <div className="title-row">
        <div>
          <h1>Citizens</h1>
          <p className="muted">Minimized synthetic registry records</p>
        </div>
        <Link className="button" to="/citizens/new">Create citizen</Link>
      </div>
      <form className="search" onSubmit={(event) => { event.preventDefault(); load(); }}>
        <input
          aria-label="Search citizens"
          placeholder="Search by name"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <button>Search</button>
      </form>
      {error && <div className="error">{error}</div>}
      <div className="table-wrap">
        <table>
          <thead><tr><th>Name</th><th>Recorded DOB</th><th>Blood type</th><th>Identifier</th></tr></thead>
          <tbody>
            {items.map((citizen) => (
              <tr key={citizen.id}>
                <td><Link to={`/citizens/${citizen.id}`}>{citizen.first_name} {citizen.last_name}</Link></td>
                <td>{citizen.date_of_birth || 'Not recorded'}</td>
                <td><strong>{citizen.blood_type}</strong></td>
                <td>{citizen.national_id_masked || 'Masked'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!items.length && <p className="empty">No citizens found.</p>}
      </div>
    </>
  );
}

function CitizenDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const isNew = id === 'new';
  const [citizen, setCitizen] = useState<Partial<Citizen>>({ blood_type: 'unknown' });
  const [nationalId, setNationalId] = useState('');
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (!isNew && id) api.citizen(id).then(setCitizen).catch((caught) => setError(caught.message));
  }, [id, isNew]);
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setError('');
    try {
      const body = {
        first_name: citizen.first_name,
        last_name: citizen.last_name,
        date_of_birth: citizen.date_of_birth,
        gender: citizen.gender,
        blood_type: citizen.blood_type,
        ...(nationalId ? { national_id: nationalId } : {}),
      };
      const savedCitizen = isNew
        ? await api.createCitizen({ ...body, national_id: nationalId })
        : await api.updateCitizen(id!, body);
      setCitizen(savedCitizen);
      setNationalId('');
      setSaved(true);
      if (isNew) navigate(`/citizens/${savedCitizen.id}`);
    } catch (caught: any) {
      setError(caught.message);
    }
  };
  return (
    <>
      <div className="title-row">
        <div>
          <Link to="/citizens">← Citizens</Link>
          <h1>{isNew ? 'Create citizen' : `${citizen.first_name || ''} ${citizen.last_name || ''}`}</h1>
        </div>
      </div>
      <div className="card">
        <form onSubmit={save} className="form-grid">
          <label>First name<input value={citizen.first_name || ''} onChange={(event) => setCitizen({ ...citizen, first_name: event.target.value })} required /></label>
          <label>Last name<input value={citizen.last_name || ''} onChange={(event) => setCitizen({ ...citizen, last_name: event.target.value })} required /></label>
          {isNew && <label>National ID (never displayed)<input value={nationalId} onChange={(event) => setNationalId(event.target.value)} required /></label>}
          {!isNew && <p className="muted">Identifier: {citizen.national_id_masked || 'masked'}</p>}
          <label>Date of birth<input type="date" value={citizen.date_of_birth || ''} onChange={(event) => setCitizen({ ...citizen, date_of_birth: event.target.value })} /></label>
          <label>Gender<select value={citizen.gender || ''} onChange={(event) => setCitizen({ ...citizen, gender: event.target.value || null })}><option value="">Unknown</option><option>female</option><option>male</option><option>non_binary</option><option>other</option><option>undisclosed</option></select></label>
          <label>Recorded blood type<select value={citizen.blood_type || 'unknown'} onChange={(event) => setCitizen({ ...citizen, blood_type: event.target.value })}>{['unknown', 'A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'].map((value) => <option key={value}>{value}</option>)}</select></label>
          <div className="form-actions"><button>Save</button>{saved && <span className="success">Saved</span>}</div>
        </form>
        {error && <div className="error">{error}</div>}
      </div>
      {!isNew && (
        <>
          <section className="card">
            <h2>Recorded allergies</h2>
            {citizen.allergies?.length
              ? <ul>{citizen.allergies.map((allergy) => <li key={allergy.id}><strong>{allergy.substance}</strong> — {allergy.status}; {allergy.reaction || 'reaction not recorded'}</li>)}</ul>
              : <p className="empty">No allergy entries recorded. This does not mean no allergies.</p>}
          </section>
          <section className="card">
            <h2>Recorded history</h2>
            {citizen.history?.length
              ? <ul>{citizen.history.map((entry) => <li key={entry.id}><strong>{entry.category}</strong> — {entry.summary} ({entry.recorded_at})</li>)}</ul>
              : <p className="empty">No history entries recorded.</p>}
          </section>
        </>
      )}
    </>
  );
}

function Hospitals() {
  const [items, setItems] = useState<Hospital[]>([]);
  const [name, setName] = useState('');
  const [code, setCode] = useState('');
  const [healthRead, setHealthRead] = useState(false);
  const [patientEnroll, setPatientEnroll] = useState(false);
  const [credential, setCredential] = useState('');
  const [error, setError] = useState('');
  const load = () => api.hospitals().then((result) => setItems(result.items)).catch((caught) => setError(caught.message));
  useEffect(() => { void load(); }, []);
  const create = async (event: FormEvent) => {
    event.preventDefault();
    setError('');
    const allowedScopes = [
      'patient.lookup',
      ...(healthRead ? ['health.read'] : []),
      ...(patientEnroll ? ['patient.enroll'] : []),
    ];
    try {
      await api.createHospital({ name, code, allowed_scopes: allowedScopes });
      setName('');
      setCode('');
      await load();
    } catch (caught: any) {
      setError(caught.message);
    }
  };
  const issue = async (hospital: Hospital) => {
    setError('');
    try {
      const result = await api.issueCredential(hospital.id, hospital.allowed_scopes);
      setCredential(`${hospital.code}: ${result.credential}`);
    } catch (caught: any) {
      setError(caught.message);
    }
  };
  const disable = async (hospital: Hospital) => {
    setError('');
    try {
      await api.updateHospital(hospital.id, { status: 'disabled' });
      await load();
    } catch (caught: any) {
      setError(caught.message);
    }
  };
  return (
    <>
      <h1>Hospitals</h1>
      <div className="split">
        <section className="card">
          <h2>Register hospital</h2>
          <form onSubmit={create}>
            <label>Code<input value={code} onChange={(event) => setCode(event.target.value.toUpperCase())} pattern="[A-Z0-9_-]{2,32}" required /></label>
            <label>Name<input value={name} onChange={(event) => setName(event.target.value)} minLength={2} required /></label>
            <label className="check-row"><input type="checkbox" checked={healthRead} onChange={(event) => setHealthRead(event.target.checked)} /> Allow `health.read` (recorded blood type, allergies, and limited history)</label>
            <label className="check-row"><input type="checkbox" checked={patientEnroll} onChange={(event) => setPatientEnroll(event.target.checked)} /> Allow `patient.enroll` (verify Central records before local Hospital enrollment)</label>
            <button>Create</button>
          </form>
        </section>
        <section className="card">
          <h2>One-time bearer credential</h2>
          <p className="muted">The 256-bit secret is shown once. Store it only in the Hospital server's approved secret store. Do not send it to a browser.</p>
          <code className="secret">{credential || 'No credential issued in this session'}</code>
        </section>
      </div>
      {error && <div className="error" role="alert">{error}</div>}
      <div className="table-wrap">
        <table>
          <thead><tr><th>Code</th><th>Name</th><th>Status</th><th>Scopes</th><th>Action</th></tr></thead>
          <tbody>
            {items.map((hospital) => (
              <tr key={hospital.id}>
                <td>{hospital.code}</td>
                <td>{hospital.name}</td>
                <td><span className={hospital.status === 'active' ? 'status' : 'status muted-status'}>{hospital.status}</span></td>
                <td>{hospital.allowed_scopes.join(', ')}</td>
                <td>
                  <button onClick={() => issue(hospital)}>Issue / rotate</button>
                  {hospital.status === 'active' && <button className="danger" onClick={() => disable(hospital)}>Disable</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!items.length && <p className="empty">No hospitals registered.</p>}
      </div>
    </>
  );
}

function Logs() {
  const [tab, setTab] = useState<'access' | 'security'>('access');
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    (tab === 'access' ? api.accessEvents() : api.securityEvents())
      .then((result) => setItems(result.items))
      .catch((caught) => setError(caught.message));
  }, [tab]);
  return (
    <>
      <h1>Logs</h1>
      <div className="tabs">
        <button className={tab === 'access' ? 'active' : ''} onClick={() => setTab('access')}>Access events</button>
        <button className={tab === 'security' ? 'active' : ''} onClick={() => setTab('security')}>Security events</button>
      </div>
      {error && <div className="error">{error}</div>}
      <div className="table-wrap">
        <table>
          <thead><tr><th>Time</th><th>Action</th><th>Outcome</th><th>Request</th><th>Redacted detail</th></tr></thead>
          <tbody>
            {items.map((item) => (
              <tr key={String(item.id)}>
                <td>{String(item.occurred_at || '')}</td>
                <td>{String(item.operation || item.action || '')}</td>
                <td>{String(item.outcome || '')}</td>
                <td><code>{String(item.request_id || '')}</code></td>
                <td>{String(item.error_code || 'No raw payload')}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!items.length && <p className="empty">No events recorded.</p>}
      </div>
    </>
  );
}

export default function App() {
  const [admin, setAdmin] = useState<Admin | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    api.session().then((result) => setAdmin(result.admin)).catch(() => undefined).finally(() => setReady(true));
  }, []);
  if (!ready) return <main className="loading">Loading secure console…</main>;
  return (
    <BrowserRouter>
      {admin
        ? <Shell admin={admin} onLogout={() => { void api.logout().then(() => setAdmin(null)); }} />
        : <Login onLogin={setAdmin} />}
    </BrowserRouter>
  );
}
