import { useState, useEffect } from 'react';
import { Download, CheckCircle, AlertCircle, RefreshCw } from 'lucide-react';
import { importApi } from '../api';

// Import fills straight from Schwab with another app's stored login
// (backend/schwab_shared.py). The journal never signs in itself: Schwab keeps one
// valid sign-in per app, so signing in here would log the other app out.
const RANGES = [
  { value: 1, label: 'Today and yesterday' },
  { value: 7, label: 'Last 7 days' },
  { value: 30, label: 'Last 30 days' },
  { value: 90, label: 'Last 90 days' },
  { value: 365, label: 'Last 365 days' },
];

export default function SchwabImport({ accounts, accountId }) {
  const [status, setStatus] = useState(null);
  const [checking, setChecking] = useState(true);
  const [journalAccount, setJournalAccount] = useState(accountId || '');
  const [schwabAccount, setSchwabAccount] = useState('');
  const [days, setDays] = useState(7);
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const check = async () => {
    setChecking(true);
    try {
      const res = await importApi.schwabStatus();
      setStatus(res.data);
      if (res.data.accounts?.length === 1) setSchwabAccount(res.data.accounts[0].hash);
    } catch (e) {
      setStatus({ ok: false, configured: true, error: e.response?.data?.error || e.message });
    } finally {
      setChecking(false);
    }
  };

  useEffect(() => { check(); }, []);

  const run = async () => {
    setImporting(true);
    setResult(null);
    setError(null);
    try {
      const res = await importApi.importSchwab({
        account_id: Number(journalAccount), schwab_account: schwabAccount, days: Number(days),
      });
      setResult(res.data);
    } catch (e) {
      setError(e.response?.data?.error || e.message);
    } finally {
      setImporting(false);
    }
  };

  const ready = status?.ok && journalAccount && schwabAccount && !importing;

  return (
    <section className="card">
      <h2 className="section-title" style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
        <Download size={18} color="var(--accent-line)" aria-hidden="true" />
        Import from Schwab
      </h2>

      {checking && <div style={{ fontSize: 14, color: 'var(--text-secondary)' }}>Checking the Schwab login...</div>}

      {!checking && status && !status.ok && (
        <div className="notice neg" role="alert">
          <AlertCircle size={14} style={{ marginRight: 6, verticalAlign: 'middle' }} />{status.error}
          <div style={{ marginTop: 8 }}>
            <button className="btn" onClick={check}><RefreshCw size={14} /> Check again</button>
          </div>
        </div>
      )}

      {!checking && status?.ok && (
        <>
          <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 12 }}>
            Uses the Schwab login from {status.app_name}, read only.
            {' '}It runs out in {status.hours_left >= 48
              ? `${Math.floor(status.hours_left / 24)} days`
              : `${Math.max(0, Math.floor(status.hours_left))} hours`}; sign in again there when it does.
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 }}>
            <div>
              <label className="field-label" htmlFor="imp-schwab-account">Schwab account</label>
              <select id="imp-schwab-account" value={schwabAccount} onChange={e => setSchwabAccount(e.target.value)}
                      style={{ width: '100%' }}>
                <option value="">Select...</option>
                {status.accounts.map(a => <option key={a.hash} value={a.hash}>{a.label}</option>)}
              </select>
            </div>
            <div>
              <label className="field-label" htmlFor="imp-schwab-journal">Into account</label>
              <select id="imp-schwab-journal" value={journalAccount} onChange={e => setJournalAccount(e.target.value)}
                      style={{ width: '100%' }}>
                <option value="">Select account...</option>
                {accounts.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            </div>
            <div>
              <label className="field-label" htmlFor="imp-schwab-days">Range</label>
              <select id="imp-schwab-days" value={days} onChange={e => setDays(e.target.value)} style={{ width: '100%' }}>
                {RANGES.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
              </select>
            </div>
          </div>

          <button className="btn btn-primary" style={{ width: '100%', justifyContent: 'center', marginTop: 16 }}
                  onClick={run} disabled={!ready}>
            {importing
              ? <><span className="spinner" style={{ width: 16, height: 16 }} /> Importing...</>
              : <><Download size={16} /> Import from Schwab</>}
          </button>
        </>
      )}

      {result && (
        <div className={`notice ${result.left_out && Object.keys(result.left_out).length ? 'neg' : 'pos'}`}
             role="status" style={{ marginTop: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 600, marginBottom: 4 }}>
            <CheckCircle size={16} /> Import Complete
          </div>
          <div style={{ fontSize: 14 }}>{result.message}</div>
          {result.errors?.length > 0 && (
            <div style={{ fontSize: 13, color: 'var(--result-neg)', marginTop: 4 }}>
              {result.errors.length} DB error(s)
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="notice neg" role="alert" style={{ marginTop: 12 }}>
          <AlertCircle size={14} style={{ marginRight: 6, verticalAlign: 'middle' }} />{error}
        </div>
      )}
    </section>
  );
}
