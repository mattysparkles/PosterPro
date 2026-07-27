import { useCallback, useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { Camera, Download, FolderSync, QrCode, Settings2 } from 'lucide-react';

import AppShell from '../components/layout/AppShell';
import Button from '../components/ui/button';
import MetricCard from '../components/ui/metric-card';
import PageHeader from '../components/ui/page-header';
import SectionPanel from '../components/ui/section-panel';
import StatusPill from '../components/ui/status-pill';
import { useAuth } from '../contexts/AuthContext';
import {
  buildIntakeExportUrl,
  fetchIntakeQueue,
  fetchIntakeSessions,
  fetchIntakeSettings,
  runIntakeMonitor,
} from '../lib/api';

function formatWhen(value) {
  if (!value) return 'Not yet synced';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export default function IntakeDashboardPage() {
  const { user } = useAuth();
  const [settings, setSettings] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [queue, setQueue] = useState({ batches: [], unassigned_photos: [] });
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);

  const load = useCallback(async () => {
    if (!user?.id) return;
    setLoading(true);
    try {
      const [settingsPayload, sessionsPayload, queuePayload] = await Promise.all([
        fetchIntakeSettings(),
        fetchIntakeSessions(),
        fetchIntakeQueue(),
      ]);
      setSettings(settingsPayload || null);
      setSessions(sessionsPayload?.sessions || []);
      setQueue({
        batches: queuePayload?.batches || [],
        unassigned_photos: queuePayload?.unassigned_photos || [],
      });
    } catch (error) {
      toast.error(error.message || 'Failed to load intake dashboard.');
    } finally {
      setLoading(false);
    }
  }, [user?.id]);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  const metrics = useMemo(() => {
    const batches = queue.batches || [];
    return {
      sessions: sessions.length,
      queued: batches.length,
      drafted: batches.filter((item) => item.status === 'drafted' || item.draft_listing_id).length,
      unassigned: (queue.unassigned_photos || []).length,
      readyToDraft: batches.filter((item) => item.status === 'ready_for_draft').length,
      photos: batches.reduce((sum, item) => sum + Number(item.photo_count || 0), 0),
    };
  }, [queue, sessions.length]);

  const syncNow = async () => {
    setSyncing(true);
    try {
      const payload = await runIntakeMonitor();
      const result = payload?.result || {};
      toast.success(`Imported ${result.imported || result.new_items || 0} photo${Number(result.imported || result.new_items || 0) === 1 ? '' : 's'} from the intake album.`);
      await load();
    } catch (error) {
      toast.error(error.message || 'Intake monitor run failed.');
    } finally {
      setSyncing(false);
    }
  };

  const latestBatches = (queue.batches || []).slice(0, 6);
  const lastMonitorResult = settings?.last_monitor_result || null;

  return (
    <AppShell active="/intake" title="Intake">
      <div className="space-y-6">
        <PageHeader
          eyebrow="Head Slate Intake"
          title="Turn a raw photo stream into review-ready drafts"
          description="PosterPro watches your linked Google Photos album or shared Drive link, detects head slates as item boundaries, groups the following product photos, and drafts listings with fulfillment metadata attached."
          actions={(
            <>
              <Button href="/intake/slate"><QrCode size={16} /> New head slate</Button>
              <Button href="/intake/queue" variant="secondary"><Camera size={16} /> Intake queue</Button>
              <Button href="/settings/intake" variant="outline"><Settings2 size={16} /> Intake settings</Button>
            </>
          )}
        />

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard label="Active sessions" value={metrics.sessions} helper="Session records available" />
          <MetricCard label="Queued item batches" value={metrics.queued} helper="Grouped by slate boundaries" />
          <MetricCard label="Ready to draft" value={metrics.readyToDraft} helper="Have slate plus product photos" />
          <MetricCard label="Unassigned photos" value={metrics.unassigned} helper="Arrived before the first slate or need manual correction" />
        </div>

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.25fr)_minmax(340px,0.75fr)]">
          <SectionPanel
            title="Album monitor"
            description="This is the live intake source. Every new Google photo is imported in source order, scanned for a slate QR payload, and then assigned to the current item batch until the next slate appears."
            action={<Button onClick={syncNow} disabled={syncing}>{syncing ? 'Running…' : 'Run monitor now'}</Button>}
          >
            <div className="grid gap-4 lg:grid-cols-2">
              <div className="rounded-[22px] border border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusPill status={settings?.enabled ? 'success' : 'warning'} label={settings?.enabled ? 'Watching enabled' : 'Watching disabled'} />
                  <StatusPill status={settings?.auto_draft_listing ? 'success' : 'default'} label={settings?.auto_draft_listing ? 'Auto draft on' : 'Auto draft off'} />
                </div>
                <dl className="mt-4 space-y-3 text-sm text-[var(--pp-muted)]">
                  <div>
                    <dt className="font-semibold text-[var(--pp-text)]">Source URL</dt>
                    <dd className="mt-1 break-all">{settings?.album_url || settings?.folder_id || 'Not configured'}</dd>
                  </div>
                  <div>
                    <dt className="font-semibold text-[var(--pp-text)]">Polling interval</dt>
                    <dd className="mt-1">{settings?.poll_interval_seconds || 300} seconds</dd>
                  </div>
                  <div>
                    <dt className="font-semibold text-[var(--pp-text)]">Marketplace targets</dt>
                    <dd className="mt-2 flex flex-wrap gap-2">
                      {(settings?.marketplace_defaults?.targets || ['ebay', 'facebook']).map((target) => (
                        <StatusPill key={target} status="default" label={String(target).replaceAll('_', ' ')} />
                      ))}
                    </dd>
                  </div>
                  <div>
                    <dt className="font-semibold text-[var(--pp-text)]">Last sync</dt>
                    <dd className="mt-1">{formatWhen(settings?.last_synced_at)}</dd>
                  </div>
                </dl>
              </div>
              <div className="rounded-[22px] border border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-4">
                <p className="text-sm font-semibold text-[var(--pp-text)]">Latest import summary</p>
                <dl className="mt-4 grid gap-3 text-sm text-[var(--pp-muted)] sm:grid-cols-2">
                  <div>
                    <dt className="font-semibold text-[var(--pp-text)]">Imported last run</dt>
                    <dd className="mt-1">{settings?.last_imported_count || 0}</dd>
                  </div>
                  <div>
                    <dt className="font-semibold text-[var(--pp-text)]">Total photos in batches</dt>
                    <dd className="mt-1">{metrics.photos}</dd>
                  </div>
                  <div>
                    <dt className="font-semibold text-[var(--pp-text)]">Drafted batches</dt>
                    <dd className="mt-1">{metrics.drafted}</dd>
                  </div>
                  <div>
                    <dt className="font-semibold text-[var(--pp-text)]">Last error</dt>
                    <dd className="mt-1">{settings?.last_error || 'None'}</dd>
                  </div>
                </dl>
                {lastMonitorResult ? (
                  <div className="mt-4 rounded-[18px] border border-[var(--pp-border)] bg-white p-4 text-sm text-[var(--pp-muted)]">
                    <p className="font-semibold text-[var(--pp-text)]">Last monitor run</p>
                    <p className="mt-2">
                      Scanned {lastMonitorResult.scanned || 0}, imported {lastMonitorResult.imported || 0}, detected {lastMonitorResult.slates_detected || 0} slate
                      {(lastMonitorResult.slates_detected || 0) === 1 ? '' : 's'}, assigned {lastMonitorResult.assigned_photos || 0} product photo
                      {(lastMonitorResult.assigned_photos || 0) === 1 ? '' : 's'}, created {lastMonitorResult.drafts_created || 0} draft
                      {(lastMonitorResult.drafts_created || 0) === 1 ? '' : 's'}.
                    </p>
                  </div>
                ) : null}
              </div>
            </div>
          </SectionPanel>

          <SectionPanel title="Operator shortcuts" description="Use these to move from photo session to listing review without hunting through the rest of the admin.">
            <div className="grid gap-3">
              <Button href="/intake/slate" className="justify-start"><QrCode size={16} /> Generate next head slate</Button>
              <Button href="/intake/queue" variant="secondary" className="justify-start"><FolderSync size={16} /> Review grouped batches</Button>
              <Button href="/settings/intake" variant="secondary" className="justify-start"><Settings2 size={16} /> Configure album monitor</Button>
              <Button href={buildIntakeExportUrl()} variant="outline" className="justify-start"><Download size={16} /> Export intake CSV</Button>
            </div>
            <div className="mt-5 rounded-[20px] border border-dashed border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-4 text-sm leading-6 text-[var(--pp-muted)]">
              <p className="font-semibold text-[var(--pp-text)]">Workflow</p>
              <ol className="mt-2 space-y-2 list-decimal pl-5">
                <li>Create or open a session.</li>
                <li>Generate a head slate for the next item.</li>
                <li>Photograph the slate, then photograph the item and packaging details.</li>
                <li>Run the intake monitor or wait for the scheduled poll.</li>
                <li>Open Intake Queue to correct grouping, regenerate the draft, and send the listing to review.</li>
              </ol>
            </div>
          </SectionPanel>
        </div>

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.1fr)_minmax(360px,0.9fr)]">
          <SectionPanel title="Recent item batches" description="These are the most recent slate-grouped items waiting for draft review or already drafted.">
            {loading ? (
              <p className="text-sm text-[var(--pp-muted)]">Loading intake queue…</p>
            ) : latestBatches.length ? (
              <div className="space-y-3">
                {latestBatches.map((batch) => (
                  <div key={batch.id} className="rounded-[20px] border border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-4">
                    <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                      <div>
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="text-sm font-semibold text-[var(--pp-text)]">{batch.item_id}</p>
                          <StatusPill status={batch.draft_listing_id ? 'success' : 'default'} label={String(batch.status || 'collecting').replaceAll('_', ' ')} />
                          {batch.slate?.box_id ? <StatusPill status="default" label={`Box ${batch.slate.box_id}`} /> : null}
                        </div>
                        <p className="mt-2 text-sm text-[var(--pp-muted)]">{batch.slate?.title || batch.listing?.title || 'Untitled intake item'}</p>
                        <p className="mt-1 text-xs text-[var(--pp-muted)]">Session {batch.session_id || batch.slate?.session_id || 'Unassigned'} · {batch.public_photo_count || 0} public photo{Number(batch.public_photo_count || 0) === 1 ? '' : 's'}</p>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <Button href="/intake/queue" variant="secondary" size="sm">Open queue</Button>
                        {batch.draft_listing_id ? <Button href={`/listings/${batch.draft_listing_id}?mode=preview`} variant="outline" size="sm">Preview draft</Button> : null}
                      </div>
                    </div>
                    {batch.warnings?.length ? (
                      <ul className="mt-3 space-y-1 text-xs text-[var(--pp-muted)]">
                        {batch.warnings.slice(0, 3).map((warning) => <li key={warning}>• {warning}</li>)}
                      </ul>
                    ) : null}
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-[20px] border border-dashed border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-6 text-sm text-[var(--pp-muted)]">
                No intake batches exist yet. Start a session and generate the first head slate.
              </div>
            )}
          </SectionPanel>

          <SectionPanel title="Sessions" description="A session groups intake work by day, tote, or storage run.">
            {sessions.length ? (
              <div className="space-y-3">
                {sessions.slice(0, 6).map((session) => (
                  <div key={session.id} className="rounded-[20px] border border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="text-sm font-semibold text-[var(--pp-text)]">{session.session_id}</p>
                        <p className="mt-1 text-sm text-[var(--pp-muted)]">{session.name || 'Unnamed session'}</p>
                        <p className="mt-1 text-xs text-[var(--pp-muted)]">Default location: {session.default_location || 'None'} · Prefixes: {session.item_prefix || 'SP'} / {session.box_prefix || 'BX'}</p>
                      </div>
                      <StatusPill status={session.status === 'active' ? 'success' : 'default'} label={session.status} />
                    </div>
                  </div>
                ))}
                <Button href="/intake/sessions" variant="outline">Manage sessions</Button>
              </div>
            ) : (
              <div className="rounded-[20px] border border-dashed border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-6 text-sm text-[var(--pp-muted)]">
                No intake sessions yet. Use the slate generator to start the first session.
              </div>
            )}
          </SectionPanel>
        </div>
      </div>
    </AppShell>
  );
}
