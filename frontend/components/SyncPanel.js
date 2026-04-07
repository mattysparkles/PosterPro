import { useState } from 'react';

import { syncSoldEverywhere } from '../lib/api';

export default function SyncPanel() {
  const [state, setState] = useState({ loading: false, message: '' });

  const runSync = async () => {
    setState({ loading: true, message: '' });
    const data = await syncSoldEverywhere();
    setState({ loading: false, message: `Sync queued: ${data.task_id}` });
  };

  return (
    <section className="card">
      <h2>Sold Everywhere Sync</h2>
      <button disabled={state.loading} onClick={runSync}>
        {state.loading ? 'Syncing...' : 'Trigger sold sync'}
      </button>
      {state.message && <p>{state.message}</p>}
    </section>
  );
}
