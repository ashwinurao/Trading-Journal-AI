import { useState, useEffect } from 'react';
import { RefreshCw, Zap } from 'lucide-react';
import { importApi } from '../api';

// Automatic import from Edge Trader (backend/edge_trader_feed.py). Edge Trader already
// polls Schwab for fills; the journal pulls them every minute and imports the new ones.
// Settings live in backend/.env, so this only shows what the server is doing.
const REFRESH_MS = 15000;
const TONE = { ok: 'pos', starting: 'accent', waiting: 'caution', error: 'neg' };

const etTime = (iso) => (iso
  ? new Date(iso).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }) + ' ET'
  : null);

export default function EdgeTraderAuto({ accounts }) {
  const [st, setSt] = useState(null);
  const [pulling, setPulling] = useState(false);

  const load = async () => {
    try {
      setSt((await importApi.edgeTraderStatus()).data);
    } catch (e) {
      setSt({ enabled: true, state: 'error', message: e.response?.data?.error || e.message });
    }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, []);

  const pull = async () => {
    setPulling(true);
    try {
      setSt((await importApi.edgeTraderPull()).data);
    } catch (e) {
      setSt(s => ({ ...s, state: 'error', message: e.response?.data?.error || e.message }));
    } finally {
      setPulling(false);
    }
  };

  if (!st) return null;

  if (!st.enabled) {
    return (
      <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 12 }} data-testid="edge-auto">
        {st.message}
      </div>
    );
  }

  const account = accounts.find(a => a.id === st.account_id);
  const bad = st.state === 'error' || st.state === 'waiting';
  return (
    <div className={`notice ${TONE[st.state] || 'pos'}`} role="status" style={{ marginBottom: 16, display: 'block' }} data-testid="edge-auto">
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 600, marginBottom: 4 }}>
        <Zap size={16} aria-hidden="true" />
        Automatic import from Edge Trader, every {st.poll_s} s{account ? ` into ${account.name}` : ''}
      </div>
      <div style={{ fontSize: 14 }}>
        {st.last_pull_at && <>Last pull {etTime(st.last_pull_at)}: </>}{st.message}
      </div>
      {bad && st.last_ok_at && (
        <div style={{ fontSize: 13, marginTop: 4 }}>Last good pull {etTime(st.last_ok_at)}.</div>
      )}
      <button className="btn" style={{ marginTop: 8 }} onClick={pull} disabled={pulling}>
        <RefreshCw size={14} /> {pulling ? 'Pulling...' : 'Pull now'}
      </button>
    </div>
  );
}
