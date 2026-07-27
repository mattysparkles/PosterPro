import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';

import AppShell from '../../components/layout/AppShell';
import Button from '../../components/ui/button';
import Input from '../../components/ui/input';
import PageHeader from '../../components/ui/page-header';
import SectionPanel from '../../components/ui/section-panel';
import StatusPill from '../../components/ui/status-pill';
import { useAuth } from '../../contexts/AuthContext';
import { createIntakeSession, fetchIntakeSessions, fetchIntakeSettings } from '../../lib/api';

const DEFAULT_FORM = {
  session_id: '',
  name: '',
  default_location: '',
  item_prefix: 'SP',
  box_prefix: 'BX',
  status: 'active',
};

export default function IntakeSessionsPage() {
  const { user } = useAuth();
  const [settings, setSettings] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [form, setForm] = useState(DEFAULT_FORM);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    if (!user?.id) return;
    try {
      const [settingsPayload, sessionsPayload] = await Promise.all([
        fetchIntakeSettings(),
        fetchIntakeSessions(),
      ]);
      setSettings(settingsPayload || null);
      setSessions(sessionsPayload?.sessions || []);
      setForm((current) => ({
        ...current,
        default_location: current.default_location || settingsPayload?.default_location || '',
        item_prefix: current.item_prefix || settingsPayload?.default_item_prefix || 'SP',
        box_prefix: current.box_prefix || settingsPayload?.default_box_prefix || 'BX',
      }));
    } catch (error) {
      toast.error(error.message || 'Failed to load intake sessions.');
    }
  }, [user?.id]);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  const submit = async () => {
    setSaving(true);
    try {
      await createIntakeSession(form);
      toast.success('Intake session saved.');
      setForm((current) => ({ ...current, session_id: '', name: '' }));
      await load();
    } catch (error) {
      toast.error(error.message || 'Failed to save intake session.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <AppShell active="/intake" title="Intake Sessions">
      <div className="space-y-6">
        <PageHeader
          eyebrow="Intake Sessions"
          title="Manage intake runs and default prefixes"
          description="Sessions let you batch work by day, storage lane, or sourcing run. Every head slate can reuse the session so grouped item batches stay organized end to end."
          actions={<Button href="/intake/slate">Open slate generator</Button>}
        />

        <div className="grid gap-6 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <SectionPanel title="Start or update a session" description="If the session ID already exists, PosterPro updates its defaults instead of creating a duplicate record.">
            <div className="grid gap-4 md:grid-cols-2">
              <label className="grid gap-2 text-sm">
                <span className="font-semibold text-[var(--pp-text)]">Session ID</span>
                <Input value={form.session_id} onChange={(event) => setForm((current) => ({ ...current, session_id: event.target.value }))} placeholder={settings?.default_session_naming_pattern || '{date}-{location}'} />
              </label>
              <label className="grid gap-2 text-sm">
                <span className="font-semibold text-[var(--pp-text)]">Name</span>
                <Input value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} placeholder="Garage shelf intake" />
              </label>
              <label className="grid gap-2 text-sm">
                <span className="font-semibold text-[var(--pp-text)]">Default location</span>
                <Input value={form.default_location} onChange={(event) => setForm((current) => ({ ...current, default_location: event.target.value }))} placeholder="A-01" />
              </label>
              <label className="grid gap-2 text-sm">
                <span className="font-semibold text-[var(--pp-text)]">Status</span>
                <select className="rounded-2xl border border-[var(--pp-border)] bg-white px-4 py-3 text-sm" value={form.status} onChange={(event) => setForm((current) => ({ ...current, status: event.target.value }))}>
                  <option value="active">active</option>
                  <option value="paused">paused</option>
                  <option value="closed">closed</option>
                </select>
              </label>
              <label className="grid gap-2 text-sm">
                <span className="font-semibold text-[var(--pp-text)]">Item prefix</span>
                <Input value={form.item_prefix} onChange={(event) => setForm((current) => ({ ...current, item_prefix: event.target.value }))} placeholder="SP" />
              </label>
              <label className="grid gap-2 text-sm">
                <span className="font-semibold text-[var(--pp-text)]">Box prefix</span>
                <Input value={form.box_prefix} onChange={(event) => setForm((current) => ({ ...current, box_prefix: event.target.value }))} placeholder="BX" />
              </label>
            </div>
            <div className="mt-5 flex flex-wrap gap-2">
              <Button onClick={submit} disabled={saving}>{saving ? 'Saving…' : 'Save session'}</Button>
              <Button href="/settings/intake" variant="outline">Intake settings</Button>
            </div>
          </SectionPanel>

          <SectionPanel title="Existing sessions" description="Most recent sessions appear first. Use these defaults when generating the next head slate.">
            {sessions.length ? (
              <div className="space-y-3">
                {sessions.map((session) => (
                  <div key={session.id} className="rounded-[20px] border border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="text-sm font-semibold text-[var(--pp-text)]">{session.session_id}</p>
                        <p className="mt-1 text-sm text-[var(--pp-muted)]">{session.name || 'Unnamed session'}</p>
                        <p className="mt-1 text-xs text-[var(--pp-muted)]">Location {session.default_location || '—'} · Prefixes {session.item_prefix || 'SP'} / {session.box_prefix || 'BX'}</p>
                      </div>
                      <StatusPill status={session.status === 'active' ? 'success' : 'default'} label={session.status} />
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-[20px] border border-dashed border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-6 text-sm text-[var(--pp-muted)]">
                No sessions yet.
              </div>
            )}
          </SectionPanel>
        </div>
      </div>
    </AppShell>
  );
}
