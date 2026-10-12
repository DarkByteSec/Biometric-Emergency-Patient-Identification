import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Link, Route, Routes, useNavigate } from 'react-router-dom';
import { api, Outcome, Patient, Scan } from './api';
import './styles.css';

type User = { id?: string; email: string; role: string };

function Layout({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate();
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);
  useEffect(() => {
    api.session()
      .then((result) => setUser(result.user))
      .catch(() => setUser(null))
      .finally(() => setChecking(false));
  }, []);
  if (checking) return <main className="loading">Loading secure hospital workspace…</main>;
  if (!user) return <Login onLogin={setUser} />;
  return (
    <div className="shell">
      <header>
        <div>
          <span className="eyebrow">HOSPITAL OPERATIONS</span>
          <h1>Safe Patient Identification</h1>
        </div>
        <div className="user">
          {user.email}
          <button onClick={() => api.logout().then(() => { setUser(null); navigate('/login'); })}>Sign out</button>
        </div>
      </header>
      <div className="warning">Synthetic/authorized test data only. This prototype is not a clinical system. A face result is only a candidate.</div>
      <nav>
        <Link to="/">Identify</Link>
        <Link to="/scans">Scan history</Link>
        {user.role === 'admin' && <Link to="/admin/recognition">Recognition admin</Link>}
      </nav>
      <main>{children}</main>
      <footer>Recorded blood type and allergies may be missing or stale. Laboratory typing, crossmatch, and clinical judgment are always required.</footer>
    </div>
  );
}

function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    setError('');
    api.login(email, password)
      .then((result) => onLogin(result.user))
      .catch((caught: Error) => setError(caught.message));
  };
  return (
    <div className="login">
      <div className="card">
        <span className="eyebrow">INDEPENDENT HOSPITAL SYSTEM</span>
        <h1>Operator sign in</h1>
        <p className="muted">Use a named hospital account. There is no public signup or default password.</p>
        <form onSubmit={submit}>
          <label>Email<input type="email" autoComplete="username" required value={email} onChange={(event) => setEmail(event.target.value)} /></label>
          <label>Password<input type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} /></label>
          {error && <div className="error" role="alert">{error}</div>}
          <button className="primary">Sign in</button>
        </form>
      </div>
    </div>
  );
}

function PatientCard({ patient }: { patient: Patient }) {
  const allergies = patient.allergies;
  return (
    <section className="patient card">
      <div className="patient-head">
        <div>
          <span className="eyebrow">ALLOWLISTED CENTRAL RECORD</span>
          <h2>{patient.display_name || 'Name not recorded'}</h2>
          <p className="muted">Record status: {patient.record_status || 'not provided'} · {patient.recorded_at ? `Recorded ${patient.recorded_at}` : 'date not provided'}</p>
        </div>
        <div className="blood"><span>RECORDED BLOOD TYPE</span><strong>{patient.blood_type || 'UNKNOWN'}</strong></div>
      </div>
      <div className="alerts">
        <div className="alert">
          <strong>Allergy warning</strong>
          <div>
            {allergies === null || allergies === undefined
              ? 'Unknown — unknown does not mean none.'
              : allergies.length
                ? allergies.map((allergy, index) => (
                    <span key={index} className="tag">
                      {allergy.name || 'Unnamed'}
                      {allergy.reaction ? ` · reaction: ${allergy.reaction}` : ''}
                      {allergy.status ? ` · ${allergy.status}` : ''}
                    </span>
                  ))
                : 'No recorded allergies (verify independently).'}
          </div>
        </div>
        <div className="alert neutral">
          <strong>Safety notice</strong>
          <div>Recorded information may be stale or incomplete. Do not diagnose or recommend treatment from this screen.</div>
        </div>
      </div>
      {patient.limited_history?.length ? (
        <div>
          <h3>Limited recorded history</h3>
          <ul>{patient.limited_history.map((entry, index) => <li key={index}>{entry.label || 'Entry'} {entry.recorded_at && <span className="muted">· {entry.recorded_at}</span>}</li>)}</ul>
        </div>
      ) : null}
    </section>
  );
}

function Identify() {
  const video = useRef<HTMLVideoElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [nationalId, setNationalId] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: string; text: string } | null>(null);
  const [patient, setPatient] = useState<Patient | null>(null);
  const [candidate, setCandidate] = useState<{ id: string } | null>(null);
  useEffect(() => () => stream?.getTracks().forEach((track) => track.stop()), [stream]);

  const stopCamera = () => {
    stream?.getTracks().forEach((track) => track.stop());
    setStream(null);
  };
  const startCamera = async () => {
    try {
      const media = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' }, audio: false });
      setStream(media);
      if (video.current) video.current.srcObject = media;
      setMessage(null);
    } catch {
      setMessage({ kind: 'error', text: 'Camera permission was not granted. Use the manual National ID path.' });
    }
  };
  const capture = () => {
    if (!video.current || !canvas.current || video.current.videoWidth < 64 || video.current.videoHeight < 64) {
      setMessage({ kind: 'error', text: 'Camera frame is not ready. Wait for the preview, then try again.' });
      return;
    }
    canvas.current.width = video.current.videoWidth;
    canvas.current.height = video.current.videoHeight;
    canvas.current.getContext('2d')?.drawImage(video.current, 0, 0);
    canvas.current.toBlob(async (blob) => {
      if (!blob) return;
      setBusy(true);
      setMessage(null);
      setPatient(null);
      try {
        const result = await api.probe(blob);
        if (result.outcome === 'candidate' && result.candidate_id) setCandidate({ id: result.candidate_id });
        else setCandidate(null);
        setMessage({ kind: result.outcome, text: outcomeText(result.outcome, result.reason_code) });
      } catch (caught: any) {
        setMessage({ kind: 'error', text: caught.message });
      } finally {
        setBusy(false);
      }
    }, 'image/jpeg', 0.9);
  };
  const lookup = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setPatient(null);
    setCandidate(null);
    setMessage(null);
    try {
      setPatient(await api.lookup(nationalId.trim()));
      setMessage({ kind: 'success', text: 'Central returned an allowlisted record. This is not proof of identity.' });
    } catch (caught: any) {
      setMessage({ kind: 'error', text: caught.message });
    } finally {
      setBusy(false);
    }
  };
  const confirm = async (decision: 'confirm' | 'reject') => {
    if (!candidate) return;
    setBusy(true);
    try {
      const result = await api.confirm(candidate.id, decision);
      setCandidate(null);
      if (result.patient) setPatient(result.patient);
      setMessage({
        kind: result.outcome,
        text: decision === 'confirm'
          ? 'Operator confirmation recorded; review the returned record carefully. This does not establish identity.'
          : 'Candidate rejected; Central retrieval was not performed.',
      });
    } catch (caught: any) {
      setMessage({ kind: 'error', text: caught.message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <section className="hero">
        <div>
          <span className="eyebrow">IDENTIFICATION WORKSPACE</span>
          <h2>Use a manual lookup or a local camera probe</h2>
          <p className="muted">A local frame is processed in memory. It is never stored or sent to Central. Face recognition, when configured, suggests a candidate only.</p>
        </div>
      </section>
      <div className="grid">
        <section className="card">
          <h3>Local camera probe</h3>
          <div className="video-wrap">
            {stream ? <video ref={video} autoPlay playsInline /> : <div className="camera-placeholder">Camera is off</div>}
          </div>
          <canvas ref={canvas} hidden />
          <div className="actions">
            <button onClick={startCamera} disabled={!!stream}>Enable camera</button>
            <button onClick={stopCamera} disabled={!stream}>Turn camera off</button>
            <button className="primary" onClick={capture} disabled={!stream || busy}>Capture frame</button>
          </div>
          <p className="muted small">No face, multiple faces, poor quality, no match, and unavailable outcomes are expected safe outcomes.</p>
        </section>
        <section className="card">
          <h3>Manual National ID lookup</h3>
          <p className="muted">Enter an authorized synthetic identifier. It is sent only in a POST JSON request; it is not placed in the URL or local scan history.</p>
          <form onSubmit={lookup}>
            <label>National ID<input value={nationalId} onChange={(event) => setNationalId(event.target.value)} minLength={3} maxLength={128} required placeholder="TEST-0001" /></label>
            <button className="primary" disabled={busy}>Look up record</button>
          </form>
        </section>
      </div>
      {message && <div className={`status ${message.kind}`} role="status">{message.text}</div>}
      {candidate && (
        <section className="candidate card">
          <h3>Candidate requires operator confirmation</h3>
          <p>This candidate is not an identity verification. Confirm only after appropriate human review, or reject to avoid Central retrieval.</p>
          <div className="actions">
            <button className="primary" onClick={() => confirm('confirm')} disabled={busy}>Confirm candidate and retrieve</button>
            <button onClick={() => confirm('reject')} disabled={busy}>Reject candidate</button>
          </div>
        </section>
      )}
      {patient && <PatientCard patient={patient} />}
    </>
  );
}

function outcomeText(outcome: Outcome, reason?: string) {
  const messages: Partial<Record<Outcome, string>> = {
    no_face: 'No face detected. Try again or use manual lookup.',
    multiple_faces: 'Multiple faces detected. Capture one person only.',
    low_quality: 'Image quality is too low. Improve lighting and framing.',
    no_match: 'No candidate match. Use manual lookup.',
    candidate: 'Candidate suggested. Operator confirmation is required.',
    unavailable: 'Recognition is unavailable; use manual lookup.',
    error: 'The frame could not be processed safely.',
    not_found: 'No record found.',
    denied: 'Central access denied.',
    timeout: 'Patient data is unavailable.',
    verified: 'Record retrieved after operator confirmation; identity is not established.',
    rejected: 'Candidate rejected.',
  };
  return messages[outcome] || `Outcome: ${outcome}${reason ? ` (${reason})` : ''}`;
}

function AdminRecognition() {
  const [citizenId, setCitizenId] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [authorizationConfirmed, setAuthorizationConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [kind, setKind] = useState('');
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setMessage('');
    try {
      const result = await api.enroll(citizenId.trim(), file, authorizationConfirmed);
      setKind(result.outcome);
      setMessage(result.outcome === 'accepted'
        ? 'Authorized sample stored locally. Retrain before probing.'
        : `Enrollment outcome: ${result.outcome}${result.reason_code ? ` (${result.reason_code})` : ''}`);
    } catch (caught: any) {
      setKind('error');
      setMessage(caught.message);
    } finally {
      setBusy(false);
    }
  };
  const retrain = async () => {
    setBusy(true);
    setMessage('');
    try {
      const result = await api.retrain();
      setKind(result.outcome);
      setMessage(result.outcome === 'trained'
        ? `Local model trained from ${result.sample_count || 0} sample(s) across ${result.subject_count || 0} subject(s). Matches remain candidates only.`
        : `Retraining outcome: ${result.outcome}${result.reason_code ? ` (${result.reason_code})` : ''}`);
    } catch (caught: any) {
      setKind('error');
      setMessage(caught.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section>
      <div className="hero">
        <div>
          <span className="eyebrow">ADMINISTRATION</span>
          <h2>Local recognition enrollment</h2>
          <p className="muted">Upload only authorized synthetic/test samples. Central first confirms the UUID through the patient.enroll scope; only the UUID is sent. Image files are cropped locally, stored in a private directory, and never sent to Central. A configured LBPH distance is an educational candidate threshold, not identity proof.</p>
        </div>
      </div>
      <div className="grid">
        <form className="card" onSubmit={submit}>
          <h3>Add an authorized sample</h3>
          <label>Central citizen ID<input value={citizenId} onChange={(event) => setCitizenId(event.target.value)} required placeholder="UUID for an authorized synthetic subject" aria-describedby="citizen-help" /></label>
          <p id="citizen-help" className="muted small">Use an existing Central citizen UUID. Central verifies that the record exists and that this Hospital credential has patient.enroll; this check is not identity proof.</p>
          <label>Face sample<input type="file" accept="image/jpeg,image/png" onChange={(event) => setFile(event.target.files?.[0] || null)} required /></label>
          <label className="check-row"><input type="checkbox" checked={authorizationConfirmed} onChange={(event) => setAuthorizationConfirmed(event.target.checked)} required /> I confirm this is an authorized synthetic/test sample and may be enrolled at this Hospital.</label>
          <button className="primary" disabled={busy || !file || !authorizationConfirmed}>Store sample locally</button>
        </form>
        <section className="card">
          <h3>Train local LBPH model</h3>
          <p className="muted">Retraining reads accepted local samples only and atomically saves the model with restrictive permissions. It never uploads samples or probes.</p>
          <button className="primary" onClick={retrain} disabled={busy}>Retrain model</button>
        </section>
      </div>
      {message && <div className={`status ${kind}`} role="status">{message}</div>}
    </section>
  );
}

function Scans() {
  const [rows, setRows] = useState<Scan[]>([]);
  const [error, setError] = useState('');
  useEffect(() => { api.scans().then((result) => setRows(result.items)).catch((caught) => setError(caught.message)); }, []);
  return (
    <section>
      <div className="hero">
        <div>
          <span className="eyebrow">LOCAL AUDIT VIEW</span>
          <h2>Scan history</h2>
          <p className="muted">Redacted operational outcomes only. National IDs, images, Central payloads, and candidate UUIDs are not displayed.</p>
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      {!error && !rows.length && <div className="empty card">No scans recorded yet.</div>}
      {rows.length > 0 && (
        <div className="table-card">
          <table>
            <thead><tr><th>Time</th><th>Method</th><th>Outcome</th><th>Central status</th><th>Candidate</th></tr></thead>
            <tbody>{rows.map((row) => (
              <tr key={row.id}>
                <td>{new Date(row.started_at).toLocaleString()}</td>
                <td>{row.method}</td>
                <td><span className={`pill ${row.outcome}`}>{row.outcome}</span></td>
                <td>{row.central_status || '—'}</td>
                <td>{row.candidate_present ? 'present' : '—'}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/login" element={<Login onLogin={() => location.assign('/')} />} />
        <Route path="/scans" element={<Scans />} />
        <Route path="/admin/recognition" element={<AdminRecognition />} />
        <Route path="*" element={<Identify />} />
      </Routes>
    </Layout>
  );
}

createRoot(document.getElementById('root')!).render(
  <BrowserRouter><App /></BrowserRouter>,
);
