import { useState } from 'react';
import { toast } from 'sonner';

import { syncSoldEverywhere } from '../lib/api';
import Button from './ui/button';
import { Card, CardDescription, CardTitle } from './ui/card';

export default function SyncPanel() {
  const [state, setState] = useState({ loading: false, message: '' });

  const runSync = async () => {
    setState({ loading: true, message: '' });
    try {
      const data = await syncSoldEverywhere();
      setState({ loading: false, message: `Sync queued: ${data.task_id}` });
      toast.success('Sold-sync queued successfully.');
    } catch (error) {
      setState({ loading: false, message: 'Sync failed. Try again.' });
      toast.error(error.message || 'Sync failed.');
    }
  };

  return (
    <Card>
      <CardTitle>Sold Everywhere Sync</CardTitle>
      <CardDescription className="mb-3">One tap to sync sold items across marketplaces.</CardDescription>
      <Button disabled={state.loading} onClick={runSync} title="Trigger synchronization for sold listings across all channels.">
        {state.loading ? 'Syncing...' : 'Trigger sold sync'}
      </Button>
      {state.message && <p className="mt-3 text-sm text-muted-foreground">{state.message}</p>}
    </Card>
  );
}
