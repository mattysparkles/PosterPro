import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';

import AppShell from '../../components/layout/AppShell';
import Button from '../../components/ui/button';
import Input from '../../components/ui/input';
import PageHeader from '../../components/ui/page-header';
import SectionPanel from '../../components/ui/section-panel';
import StatusPill from '../../components/ui/status-pill';
import { useAuth } from '../../contexts/AuthContext';
import { fetchIntakeSettings, runIntakeMonitor, updateIntakeSettings } from '../../lib/api';

const MARKETPLACE_TARGET_OPTIONS = [
  { value: 'ebay', label: 'eBay' },
  { value: 'facebook', label: 'Facebook Marketplace' },
  { value: 'mercari', label: 'Mercari' },
  { value: 'poshmark', label: 'Poshmark' },
  { value: 'etsy', label: 'Etsy' },
  { value: 'depop', label: 'Depop' },
  { value: 'whatnot', label: 'Whatnot' },
];

export default function IntakeSettingsPage() {
  const { user } = useAuth();
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);

  const load = useCallback(async () => {
    if (!user?.id) return;
    try {
      const payload = await fetchIntakeSettings();
      setForm(payload || null);
    } catch (error) {
      toast.error(error.message || 'Failed to load intake settings.');
    }
  }, [user?.id]);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  const updateField = (key, value) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const toggleMarketplaceTarget = (target) => {
    const currentTargets = Array.isArray(form?.marketplace_defaults?.targets) ? form.marketplace_defaults.targets : [];
    const nextTargets = currentTargets.includes(target)
      ? currentTargets.filter((value) => value !== target)
      : [...currentTargets, target];
    updateField('marketplace_defaults', {
      ...(form.marketplace_defaults || {}),
      targets: nextTargets,
    });
  };

  const save = async () => {
    setSaving(true);
    try {
      const payload = await updateIntakeSettings(form);
      setForm(payload || null);
      toast.success('Intake settings saved.');
    } catch (error) {
      toast.error(error.message || 'Failed to save intake settings.');
    } finally {
      setSaving(false);
    }
  };

  const runNow = async () => {
    setRunning(true);
    try {
      await runIntakeMonitor();
      toast.success('Intake monitor completed.');
      await load();
    } catch (error) {
      toast.error(error.message || 'Intake monitor failed.');
    } finally {
      setRunning(false);
    }
  };

  if (!form) {
    return (
      <AppShell active="/settings" title="Intake Settings">
        <div className="space-y-6">
          <PageHeader eyebrow="Settings" title="Intake settings" description="Loading intake settings…" />
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell active="/settings" title="Intake Settings">
      <div className="space-y-6">
          <PageHeader
            eyebrow="Settings"
            title="Configure the Head Slate intake monitor"
            description="These settings control the monitored Google Photos album or shared Drive link, ID generation, default storage metadata, and whether PosterPro drafts listings automatically after a complete batch is detected."
            actions={(
            <>
              <Button onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save settings'}</Button>
              <Button onClick={runNow} variant="secondary" disabled={running}>{running ? 'Running…' : 'Run monitor now'}</Button>
            </>
          )}
        />

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.1fr)_minmax(360px,0.9fr)]">
          <SectionPanel title="Album and monitoring" description="PosterPro stores these settings on the user record and uses them for manual and automatic intake monitor runs.">
            <div className="grid gap-4 md:grid-cols-2">
              <label className="grid gap-2 text-sm md:col-span-2">
                <span className="font-semibold text-[var(--pp-text)]">Google Photos album URL</span>
                <Input value={form.album_url || ''} onChange={(event) => updateField('album_url', event.target.value)} placeholder="https://photos.app.goo.gl/..." />
              </label>
              <label className="grid gap-2 text-sm">
                <span className="font-semibold text-[var(--pp-text)]">Google Drive folder / shared link</span>
                <Input value={form.folder_id || ''} onChange={(event) => updateField('folder_id', event.target.value)} placeholder="Optional alternate source link" />
              </label>
              <label className="grid gap-2 text-sm">
                <span className="font-semibold text-[var(--pp-text)]">Polling interval seconds</span>
                <Input type="number" value={form.poll_interval_seconds || 300} onChange={(event) => updateField('poll_interval_seconds', Number(event.target.value || 300))} />
              </label>
              <label className="flex items-center gap-3 text-sm text-[var(--pp-text)] md:col-span-2">
                <input type="checkbox" checked={Boolean(form.enabled)} onChange={(event) => updateField('enabled', event.target.checked)} />
                <span>Enable automatic intake monitoring</span>
              </label>
              <label className="flex items-center gap-3 text-sm text-[var(--pp-text)] md:col-span-2">
                <input type="checkbox" checked={Boolean(form.auto_draft_listing)} onChange={(event) => updateField('auto_draft_listing', event.target.checked)} />
                <span>Auto-create draft listings when a batch has product photos</span>
              </label>
              <label className="flex items-center gap-3 text-sm text-[var(--pp-text)] md:col-span-2">
                <input type="checkbox" checked={Boolean(form.require_manual_review_before_publish)} onChange={(event) => updateField('require_manual_review_before_publish', event.target.checked)} />
                <span>Require manual review before any marketplace publish step</span>
              </label>
            </div>
          </SectionPanel>

          <SectionPanel title="Current status" description="This is the last known intake runtime state.">
            <div className="flex flex-wrap gap-2">
              <StatusPill status={form.enabled ? 'success' : 'warning'} label={form.enabled ? 'Monitor enabled' : 'Monitor disabled'} />
              <StatusPill status={form.auto_draft_listing ? 'success' : 'default'} label={form.auto_draft_listing ? 'Auto draft on' : 'Auto draft off'} />
            </div>
            <dl className="mt-4 space-y-3 text-sm text-[var(--pp-muted)]">
              <div>
                <dt className="font-semibold text-[var(--pp-text)]">Last sync</dt>
                <dd className="mt-1">{form.last_synced_at || 'Never'}</dd>
              </div>
              <div>
                <dt className="font-semibold text-[var(--pp-text)]">Last imported count</dt>
                <dd className="mt-1">{form.last_imported_count || 0}</dd>
              </div>
              <div>
                <dt className="font-semibold text-[var(--pp-text)]">Last error</dt>
                <dd className="mt-1">{form.last_error || 'None'}</dd>
              </div>
            </dl>
          </SectionPanel>
        </div>

        <div className="grid gap-6 xl:grid-cols-2">
          <SectionPanel title="ID generation defaults" description="Item IDs remain permanent inventory IDs. Box IDs identify the packed physical container.">
            <div className="grid gap-4 md:grid-cols-2">
              <label className="grid gap-2 text-sm">
                <span className="font-semibold text-[var(--pp-text)]">Default item prefix</span>
                <Input value={form.default_item_prefix || 'SP'} onChange={(event) => updateField('default_item_prefix', event.target.value)} />
              </label>
              <label className="grid gap-2 text-sm">
                <span className="font-semibold text-[var(--pp-text)]">Default box prefix</span>
                <Input value={form.default_box_prefix || 'BX'} onChange={(event) => updateField('default_box_prefix', event.target.value)} />
              </label>
              <label className="grid gap-2 text-sm">
                <span className="font-semibold text-[var(--pp-text)]">Default location</span>
                <Input value={form.default_location || ''} onChange={(event) => updateField('default_location', event.target.value)} />
              </label>
              <label className="grid gap-2 text-sm">
                <span className="font-semibold text-[var(--pp-text)]">Session naming pattern</span>
                <Input value={form.default_session_naming_pattern || '{date}-{location}'} onChange={(event) => updateField('default_session_naming_pattern', event.target.value)} />
              </label>
              <label className="flex items-center gap-3 text-sm text-[var(--pp-text)] md:col-span-2">
                <input type="checkbox" checked={Boolean(form.auto_increment_item_id)} onChange={(event) => updateField('auto_increment_item_id', event.target.checked)} />
                <span>Auto-increment item IDs</span>
              </label>
              <label className="flex items-center gap-3 text-sm text-[var(--pp-text)] md:col-span-2">
                <input type="checkbox" checked={Boolean(form.auto_increment_box_id)} onChange={(event) => updateField('auto_increment_box_id', event.target.checked)} />
                <span>Auto-increment box IDs</span>
              </label>
              <label className="flex items-center gap-3 text-sm text-[var(--pp-text)] md:col-span-2">
                <input type="checkbox" checked={Boolean(form.keep_same_box_mode)} onChange={(event) => updateField('keep_same_box_mode', event.target.checked)} />
                <span>Default to same box / tote mode</span>
              </label>
            </div>
          </SectionPanel>

          <SectionPanel title="Image and publish defaults" description="These defaults decide which images stay internal and how the first generated draft gets staged for marketplace review.">
            <div className="grid gap-4">
              <label className="grid gap-2 text-sm">
                <span className="font-semibold text-[var(--pp-text)]">Image SEO filename pattern</span>
                <Input value={form.image_seo_filename_pattern || '{item_id}_{seo_title}_{photo_number}'} onChange={(event) => updateField('image_seo_filename_pattern', event.target.value)} />
              </label>
              <label className="flex items-center gap-3 text-sm text-[var(--pp-text)]">
                <input type="checkbox" checked={Boolean(form.exclude_head_slate_from_public_listing_photos)} onChange={(event) => updateField('exclude_head_slate_from_public_listing_photos', event.target.checked)} />
                <span>Exclude head slate images from public listing photos</span>
              </label>
              <label className="flex items-center gap-3 text-sm text-[var(--pp-text)]">
                <input type="checkbox" checked={Boolean(form.internal_box_photos_default)} onChange={(event) => updateField('internal_box_photos_default', event.target.checked)} />
                <span>Treat packed box / label photos as internal-only by default</span>
              </label>
              <label className="grid gap-2 text-sm">
                <span className="font-semibold text-[var(--pp-text)]">Marketplace targets</span>
                <div className="grid gap-2 rounded-[20px] border border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-4 sm:grid-cols-2">
                  {MARKETPLACE_TARGET_OPTIONS.map((target) => {
                    const checked = (form.marketplace_defaults?.targets || ['ebay', 'facebook']).includes(target.value);
                    return (
                      <label key={target.value} className="flex items-center gap-3 text-sm text-[var(--pp-text)]">
                        <input type="checkbox" checked={checked} onChange={() => toggleMarketplaceTarget(target.value)} />
                        <span>{target.label}</span>
                      </label>
                    );
                  })}
                </div>
              </label>
            </div>
          </SectionPanel>
        </div>
      </div>
    </AppShell>
  );
}
