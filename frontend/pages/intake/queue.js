import { useCallback, useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { FilePlus2, FolderSync, GitFork, ShieldAlert } from 'lucide-react';

import AppShell from '../../components/layout/AppShell';
import Button from '../../components/ui/button';
import PageHeader from '../../components/ui/page-header';
import SectionPanel from '../../components/ui/section-panel';
import StatusPill from '../../components/ui/status-pill';
import { useAuth } from '../../contexts/AuthContext';
import {
  applyIntakePhotoBoundaries,
  assignIntakeUnassignedPhotos,
  draftIntakeBatch,
  fetchIntakeQueue,
  runIntakeMonitor,
  reconcileIntakeTimeline,
  runIntakeIntegrityScan,
  syncIntakeAlbumTruth,
  toPublicImageUrl,
  updateIntakePhoto,
  updateIntakeSlate,
} from '../../lib/api';

function resolvePhotoUrl(photo) {
  return photo?.thumbnail_url || photo?.display_url || photo?.local_path || '';
}

function UnassignedPhotoCard({
  photo,
  selected,
  boundaryTarget,
  slateLabel,
  assigning,
  onToggle,
  onMarkBoundary,
  onAssignOne,
}) {
  return (
    <div
      className={`overflow-hidden rounded-[20px] border text-sm transition ${
        selected
          ? 'border-[var(--pp-primary)] bg-white shadow-[0_0_0_3px_rgba(11,61,145,0.12)]'
          : 'border-[var(--pp-border)] bg-[var(--pp-surface-muted)] text-[var(--pp-muted)]'
      }`}
    >
      <div className="aspect-[4/3] bg-white">
        {resolvePhotoUrl(photo) ? <img src={toPublicImageUrl(resolvePhotoUrl(photo))} alt={photo.original_filename || `Photo ${photo.id}`} className="h-full w-full object-cover" /> : null}
      </div>
      <div className="p-3">
        <div className="flex flex-wrap items-center gap-2">
          <StatusPill status={photo.is_internal_only ? 'default' : 'warning'} label={photo.is_internal_only ? 'Internal' : 'Unassigned'} />
          {photo.metadata_json?.slate_detection_result === 'probable_slate_candidate' ? <StatusPill status="warning" label="Slate candidate" /> : null}
          {selected ? <StatusPill status="success" label="Selected" /> : null}
          {boundaryTarget ? <StatusPill status="success" label={`Boundary queued`} /> : null}
        </div>
        <p className="mt-2 font-semibold text-[var(--pp-text)]">Photo #{photo.id}</p>
        <p className="mt-1 line-clamp-2">{photo.original_filename || photo.source_photo_id}</p>
        <p className="mt-1 text-xs text-[var(--pp-muted)]">Stream order: {photo.imported_at || photo.created_at || 'unknown'}</p>
        <div className="mt-3 grid grid-cols-3 gap-2">
          <Button size="sm" variant={selected ? 'secondary' : 'outline'} onClick={() => onToggle(photo.id)}>
            {selected ? 'Selected' : 'Select'}
          </Button>
          <Button size="sm" variant="outline" onClick={() => onMarkBoundary(photo.id)} disabled={assigning || !slateLabel}>
            {boundaryTarget ? 'Queued' : 'Boundary'}
          </Button>
          <Button size="sm" variant="secondary" onClick={() => onAssignOne(photo.id)} disabled={assigning || !slateLabel}>
            Assign
          </Button>
        </div>
        {boundaryTarget ? (
          <p className="mt-2 text-xs text-[var(--pp-muted)]">Queued boundary target: {boundaryTarget}</p>
        ) : slateLabel ? (
          <p className="mt-2 text-xs text-[var(--pp-muted)]">Target slate: {slateLabel}</p>
        ) : (
          <p className="mt-2 text-xs text-amber-700">Pick a saved slate first.</p>
        )}
      </div>
    </div>
  );
}

function BatchCard({ batch, onRefresh }) {
  const [saving, setSaving] = useState(false);
  const [slateForm, setSlateForm] = useState({
    box_id: batch?.slate?.box_id || '',
    location: batch?.slate?.location || '',
    title: batch?.slate?.title || '',
    condition: batch?.slate?.condition || '',
  });

  const saveSlate = async () => {
    if (!batch?.slate?.id) return;
    setSaving(true);
    try {
      await updateIntakeSlate(batch.slate.id, slateForm);
      toast.success(`Updated slate ${batch.item_id}.`);
      await onRefresh();
    } catch (error) {
      toast.error(error.message || 'Failed to update slate.');
    } finally {
      setSaving(false);
    }
  };

  const makeDraft = async () => {
    setSaving(true);
    try {
      await draftIntakeBatch(batch.id, { force_regenerate: true });
      toast.success(`Draft regenerated for ${batch.item_id}.`);
      await onRefresh();
    } catch (error) {
      toast.error(error.message || 'Failed to generate draft from batch.');
    } finally {
      setSaving(false);
    }
  };

  const setPhotoMode = async (photoId, mode) => {
    setSaving(true);
    try {
      if (mode === 'public') {
        await updateIntakePhoto(photoId, {
          is_slate: false,
          is_internal_only: false,
          is_public_listing_candidate: true,
          image_type: 'product',
        });
      } else if (mode === 'internal') {
        await updateIntakePhoto(photoId, {
          is_slate: false,
          is_internal_only: true,
          is_public_listing_candidate: false,
          image_type: 'internal',
        });
      } else if (mode === 'slate') {
        await updateIntakePhoto(photoId, {
          is_slate: true,
          is_internal_only: true,
          is_public_listing_candidate: false,
          image_type: 'slate',
        });
      }
      await onRefresh();
    } catch (error) {
      toast.error(error.message || 'Failed to update intake photo.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded-[24px] border border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-semibold text-[var(--pp-text)]">{batch.item_id}</p>
            <StatusPill status={batch.draft_listing_id ? 'success' : batch.public_photo_count ? 'default' : 'warning'} label={String(batch.status || 'collecting').replaceAll('_', ' ')} />
            {batch.slate?.box_id ? <StatusPill status="default" label={`Box ${batch.slate.box_id}`} /> : null}
            {batch.slate?.location ? <StatusPill status="default" label={`Loc ${batch.slate.location}`} /> : null}
          </div>
          <p className="mt-2 text-base font-semibold text-[var(--pp-text)]">{batch.slate?.title || batch.listing?.title || 'Untitled intake item'}</p>
          <p className="mt-1 text-sm text-[var(--pp-muted)]">Session {batch.session_id || batch.slate?.session_id || 'Unassigned'} · {batch.public_photo_count || 0} public photo{Number(batch.public_photo_count || 0) === 1 ? '' : 's'} · {batch.internal_photo_count || 0} internal photo{Number(batch.internal_photo_count || 0) === 1 ? '' : 's'}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={makeDraft} disabled={saving}><FilePlus2 size={16} /> Regenerate draft</Button>
          {batch.draft_listing_id ? <Button href={`/listings/${batch.draft_listing_id}?mode=preview`} variant="secondary">Marketplace preview</Button> : null}
          <Button href="/listings" variant="secondary">Review listings</Button>
        </div>
      </div>

      <div className="mt-5 grid gap-4 xl:grid-cols-[minmax(0,0.7fr)_minmax(0,1.3fr)]">
        <div className="space-y-4 rounded-[20px] border border-[var(--pp-border)] bg-white p-4">
          <p className="text-sm font-semibold text-[var(--pp-text)]">Slate details</p>
          {batch.slate_photo ? (
            <div className="overflow-hidden rounded-[18px] border border-[var(--pp-border)] bg-[var(--pp-surface-muted)]">
              <div className="aspect-[4/3] bg-[var(--pp-surface-muted)]">
                {resolvePhotoUrl(batch.slate_photo) ? <img src={toPublicImageUrl(resolvePhotoUrl(batch.slate_photo))} alt={batch.slate?.item_id || 'Slate photo'} className="h-full w-full object-cover" /> : null}
              </div>
              <div className="flex items-center justify-between gap-2 px-3 py-2 text-xs text-[var(--pp-muted)]">
                <span className="font-semibold text-[var(--pp-text)]">Slate boundary photo</span>
                <StatusPill status={batch.slate_photo.is_slate ? 'warning' : 'default'} label={batch.slate_photo.metadata_json?.slate_detection_result || 'slate'} />
              </div>
            </div>
          ) : null}
          <div className="grid gap-3 text-sm md:grid-cols-2 xl:grid-cols-1">
            <label className="grid gap-2">
              <span className="font-semibold text-[var(--pp-text)]">Box ID</span>
              <input className="rounded-2xl border border-[var(--pp-border)] px-4 py-3" value={slateForm.box_id} onChange={(event) => setSlateForm((current) => ({ ...current, box_id: event.target.value }))} />
            </label>
            <label className="grid gap-2">
              <span className="font-semibold text-[var(--pp-text)]">Location</span>
              <input className="rounded-2xl border border-[var(--pp-border)] px-4 py-3" value={slateForm.location} onChange={(event) => setSlateForm((current) => ({ ...current, location: event.target.value }))} />
            </label>
            <label className="grid gap-2 md:col-span-2 xl:col-span-1">
              <span className="font-semibold text-[var(--pp-text)]">Title</span>
              <input className="rounded-2xl border border-[var(--pp-border)] px-4 py-3" value={slateForm.title} onChange={(event) => setSlateForm((current) => ({ ...current, title: event.target.value }))} />
            </label>
            <label className="grid gap-2 md:col-span-2 xl:col-span-1">
              <span className="font-semibold text-[var(--pp-text)]">Condition</span>
              <input className="rounded-2xl border border-[var(--pp-border)] px-4 py-3" value={slateForm.condition} onChange={(event) => setSlateForm((current) => ({ ...current, condition: event.target.value }))} />
            </label>
          </div>
          <Button onClick={saveSlate} variant="outline" disabled={saving}>Save slate edits</Button>
          {batch.warnings?.length ? (
            <div className="rounded-[18px] border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
              <p className="font-semibold">Warnings</p>
              <ul className="mt-2 space-y-1">
                {batch.warnings.map((warning) => <li key={warning}>• {warning}</li>)}
              </ul>
            </div>
          ) : null}
        </div>

        <div className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm font-semibold text-[var(--pp-text)]">Batch photos</p>
            <p className="text-xs text-[var(--pp-muted)]">Manual correction tools: mark a photo as slate, public product photo, or internal-only evidence.</p>
          </div>
          <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
            {(batch.photos || []).map((photo) => (
              <div key={photo.id} className="rounded-[20px] border border-[var(--pp-border)] bg-white p-3">
                <div className="aspect-square overflow-hidden rounded-[16px] bg-[var(--pp-surface-muted)]">
                  {resolvePhotoUrl(photo) ? <img src={toPublicImageUrl(resolvePhotoUrl(photo))} alt={photo.original_filename || `Intake photo ${photo.id}`} className="h-full w-full object-cover" /> : null}
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <StatusPill status={photo.is_slate ? 'warning' : photo.is_internal_only ? 'default' : 'success'} label={photo.is_slate ? 'Slate' : photo.is_internal_only ? 'Internal only' : 'Public'} />
                  {photo.metadata_json?.slate_detection_result === 'probable_slate_candidate' ? <StatusPill status="warning" label="Slate candidate" /> : null}
                  {photo.image_type ? <StatusPill status="default" label={photo.image_type} /> : null}
                </div>
                <p className="mt-3 line-clamp-2 text-xs text-[var(--pp-muted)]">{photo.original_filename || photo.source_photo_id}</p>
                <div className="mt-3 grid grid-cols-3 gap-2">
                  <Button size="sm" variant="secondary" onClick={() => setPhotoMode(photo.id, 'public')} disabled={saving}>Public</Button>
                  <Button size="sm" variant="outline" onClick={() => setPhotoMode(photo.id, 'internal')} disabled={saving}>Internal</Button>
                  <Button size="sm" variant="outline" onClick={() => setPhotoMode(photo.id, 'slate')} disabled={saving}>Slate</Button>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function IntakeQueuePage() {
  const { user } = useAuth();
  const [queue, setQueue] = useState({ batches: [], unassigned_photos: [], available_slates: [], slate_candidates: [] });
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [truthSyncing, setTruthSyncing] = useState(false);
  const [reconciling, setReconciling] = useState(false);
  const [selectedSlate, setSelectedSlate] = useState('');
  const [assigning, setAssigning] = useState(false);
  const [selectedPhotoIds, setSelectedPhotoIds] = useState([]);
  const [stagedBoundaries, setStagedBoundaries] = useState({});

  const load = useCallback(async () => {
    if (!user?.id) return;
    setLoading(true);
    try {
      const payload = await fetchIntakeQueue();
      setQueue({
        batches: payload?.batches || [],
        unassigned_photos: payload?.unassigned_photos || [],
        available_slates: payload?.available_slates || [],
        slate_candidates: payload?.slate_candidates || [],
      });
      setSelectedPhotoIds((current) => {
        const validIds = new Set((payload?.unassigned_photos || []).map((photo) => photo.id));
        return current.filter((photoId) => validIds.has(photoId));
      });
      setStagedBoundaries((current) => {
        const validIds = new Set([
          ...((payload?.unassigned_photos || []).map((photo) => photo.id)),
          ...((payload?.slate_candidates || []).map((photo) => photo.id)),
        ]);
        return Object.fromEntries(Object.entries(current).filter(([photoId]) => validIds.has(Number(photoId))));
      });
    } catch (error) {
      toast.error(error.message || 'Failed to load intake queue.');
    } finally {
      setLoading(false);
    }
  }, [user?.id]);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  const summary = useMemo(() => ({
    batches: (queue.batches || []).length,
    drafted: (queue.batches || []).filter((item) => item.draft_listing_id).length,
    ready: (queue.batches || []).filter((item) => item.status === 'ready_for_draft').length,
    unassigned: (queue.unassigned_photos || []).length,
    slateCandidates: (queue.slate_candidates || []).length,
  }), [queue]);

  useEffect(() => {
    if (selectedSlate) return;
    const firstAvailable = (queue.available_slates || []).find((slate) => !slate.listing_id)?.item_id || queue.available_slates?.[0]?.item_id || '';
    if (firstAvailable) setSelectedSlate(firstAvailable);
  }, [queue.available_slates, selectedSlate]);

  const selectedSlateLabel = useMemo(() => {
    const slate = (queue.available_slates || []).find((row) => row.item_id === selectedSlate);
    if (!slate) return '';
    return `${slate.item_id} - ${slate.title || slate.location || 'Untitled slate'}`;
  }, [queue.available_slates, selectedSlate]);

  const toggleSelectedPhoto = (photoId) => {
    setSelectedPhotoIds((current) => (
      current.includes(photoId)
        ? current.filter((value) => value !== photoId)
        : [...current, photoId]
    ));
  };

  const syncNow = async () => {
    setSyncing(true);
    try {
      await runIntakeMonitor();
      toast.success('Intake monitor completed.');
      await load();
    } catch (error) {
      toast.error(error.message || 'Intake monitor failed.');
    } finally {
      setSyncing(false);
    }
  };

  const syncAlbumTruth = async () => {
    setTruthSyncing(true);
    try {
      const payload = await syncIntakeAlbumTruth();
      const result = payload?.result || {};
      toast.success(`Synced album truth. Removed ${result.removed || 0} stale intake photos and preserved ${result.preserved || 0} already-linked rows.`);
      setSelectedPhotoIds([]);
      await load();
    } catch (error) {
      toast.error(error.message || 'Album truth sync failed.');
    } finally {
      setTruthSyncing(false);
    }
  };

  const reconcileTimeline = async () => {
    setReconciling(true);
    try {
      const payload = await runIntakeIntegrityScan();
      const interval = payload?.interval || {};
      toast.success(`Reconciled ${interval.photo_count || 0} intake photos in capture-time order.`);
      await load();
    } catch (error) {
      toast.error(error.message || 'Timeline reconciliation failed.');
    } finally {
      setReconciling(false);
    }
  };

  const assignUnassigned = async () => {
    if (!selectedSlate) {
      toast.error('Select a saved slate first.');
      return;
    }
    if (!selectedPhotoIds.length) {
      toast.error('Select one or more unassigned photos first.');
      return;
    }
    setAssigning(true);
    try {
      await assignIntakeUnassignedPhotos({
        item_id: selectedSlate,
        photo_ids: selectedPhotoIds,
        mark_ready_for_draft: true,
      });
      toast.success(`Assigned ${selectedPhotoIds.length} selected photos to ${selectedSlate} and generated drafts where grouping is now complete.`);
      setSelectedPhotoIds([]);
      await load();
    } catch (error) {
      toast.error(error.message || 'Failed to assign unassigned intake photos.');
    } finally {
      setAssigning(false);
    }
  };

  const assignSinglePhoto = async (photoId) => {
    if (!selectedSlate) {
      toast.error('Select a saved slate first.');
      return;
    }
    setAssigning(true);
    try {
      await assignIntakeUnassignedPhotos({
        item_id: selectedSlate,
        photo_ids: [photoId],
        mark_ready_for_draft: true,
      });
      toast.success(`Assigned photo ${photoId} to ${selectedSlate}.`);
      setSelectedPhotoIds((current) => current.filter((value) => value !== photoId));
      await load();
    } catch (error) {
      toast.error(error.message || 'Failed to assign the selected photo.');
    } finally {
      setAssigning(false);
    }
  };

  const markPhotoAsBoundary = async (photoId) => {
    if (!selectedSlate) {
      toast.error('Select a saved slate first.');
      return;
    }
    setStagedBoundaries((current) => ({ ...current, [photoId]: selectedSlate }));
    setSelectedPhotoIds((current) => current.filter((value) => value !== photoId));
    toast.success(`Queued photo ${photoId} as a boundary for ${selectedSlate}. Apply staged boundaries when ready.`);
  };

  const applyBoundarySelections = async () => {
    const boundaryEntries = Object.entries(stagedBoundaries);
    if (!boundaryEntries.length) {
      toast.error('Queue one or more boundary photos first.');
      return;
    }
    setAssigning(true);
    try {
      const payload = await applyIntakePhotoBoundaries({
        boundaries: boundaryEntries.map(([photoId, itemId]) => ({
          photo_id: Number(photoId),
          item_id: itemId,
        })),
        mark_ready_for_draft: true,
      });
      const result = payload?.result || {};
      toast.success(`Applied ${result.boundaries_applied || boundaryEntries.length} boundary selections in one regroup pass.`);
      setStagedBoundaries({});
      setSelectedPhotoIds([]);
      await load();
    } catch (error) {
      toast.error(error.message || 'Failed to apply staged boundary selections.');
    } finally {
      setAssigning(false);
    }
  };

  return (
    <AppShell active="/intake" title="Intake Queue" contentWidth="wide">
      <div className="space-y-6">
        <PageHeader
          eyebrow="Intake Queue"
          title="Review grouped item batches before publish review"
          description="This queue is where slate grouping gets corrected, internal-only images are removed from public photo sets, and the final draft listing is generated before the normal PosterPro listing review flow takes over."
          actions={(
            <>
              <Button onClick={syncNow} disabled={syncing}><FolderSync size={16} /> {syncing ? 'Running…' : 'Run monitor now'}</Button>
              <Button onClick={syncAlbumTruth} variant="outline" disabled={truthSyncing}>{truthSyncing ? 'Syncing…' : 'Sync current album truth'}</Button>
              <Button onClick={reconcileTimeline} variant="outline" disabled={reconciling}><GitFork size={16} /> {reconciling ? 'Reconciling…' : 'Reconcile capture timeline'}</Button>
              <Button href="/intake/slate" variant="secondary">New slate</Button>
            </>
          )}
        />

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
          <SectionPanel title="Grouped batches"><p className="text-3xl font-semibold text-[var(--pp-text)]">{summary.batches}</p></SectionPanel>
          <SectionPanel title="Ready for draft"><p className="text-3xl font-semibold text-[var(--pp-text)]">{summary.ready}</p></SectionPanel>
          <SectionPanel title="Drafted batches"><p className="text-3xl font-semibold text-[var(--pp-text)]">{summary.drafted}</p></SectionPanel>
          <SectionPanel title="Unassigned photos"><p className="text-3xl font-semibold text-[var(--pp-text)]">{summary.unassigned}</p></SectionPanel>
          <SectionPanel title="Slate candidates"><p className="text-3xl font-semibold text-[var(--pp-text)]">{summary.slateCandidates}</p></SectionPanel>
        </div>

        <SectionPanel title="Timeline safety" description="PosterPro groups from original capture chronology, not upload arrival order. Use reconciliation after late uploads, a recovered slate, or manual boundary corrections.">
          <div className="flex flex-col gap-3 rounded-[20px] border border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-4 lg:flex-row lg:items-center lg:justify-between">
            <p className="max-w-3xl text-sm text-[var(--pp-muted)]">A reconciliation preserves operator assignments and published listings. If late evidence affects a published item, PosterPro creates a review event instead of silently updating the marketplace listing.</p>
            <Button onClick={reconcileTimeline} variant="secondary" disabled={reconciling}>{reconciling ? 'Reconciling…' : 'Run integrity scan'}</Button>
          </div>
        </SectionPanel>

        {queue.slate_candidates?.length ? (
          <SectionPanel title="Slate candidates" description="These images look like photographed head slates or slate screenshots. Review them first so the intake stream can be split at the correct boundary.">
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {queue.slate_candidates.map((photo) => (
                <UnassignedPhotoCard
                  key={photo.id}
                  photo={photo}
                  selected={selectedPhotoIds.includes(photo.id)}
                  boundaryTarget={stagedBoundaries[photo.id] || ''}
                  slateLabel={selectedSlateLabel}
                  assigning={assigning}
                  onToggle={toggleSelectedPhoto}
                  onMarkBoundary={markPhotoAsBoundary}
                  onAssignOne={assignSinglePhoto}
                />
              ))}
            </div>
          </SectionPanel>
        ) : null}

        {queue.unassigned_photos?.length ? (
          <SectionPanel title="Unassigned intake photos" description="These arrived before the first slate boundary or lost their batch assignment. Reassign them manually after confirming the correct item.">
            <div className="mb-4 grid gap-3 rounded-[20px] border border-amber-200 bg-amber-50 p-4 lg:grid-cols-[minmax(0,1fr)_auto]">
              <div>
                <p className="text-sm font-semibold text-amber-900">No head slate boundary was detected for these photos.</p>
                <p className="mt-1 text-sm text-amber-800">Pick a saved PosterPro slate, then mark the photographed slate thumbnail as the boundary for that item. PosterPro will regroup every following photo under that item until the next marked boundary and auto-draft the listing once a complete batch exists.</p>
              </div>
              <div className="flex flex-col gap-2 lg:min-w-[280px]">
                <select
                  className="rounded-2xl border border-amber-200 bg-white px-4 py-3 text-sm text-[#101828]"
                  value={selectedSlate}
                  onChange={(event) => setSelectedSlate(event.target.value)}
                >
                  <option value="">Select saved slate</option>
                  {(queue.available_slates || []).map((slate) => (
                    <option key={slate.id} value={slate.item_id}>
                      {slate.item_id} - {slate.title || slate.location || 'Untitled slate'}
                    </option>
                  ))}
                </select>
                <Button onClick={assignUnassigned} disabled={assigning || !selectedSlate}>
                  {assigning ? 'Assigning…' : `Assign ${selectedPhotoIds.length || 0} selected product photos`}
                </Button>
                <Button onClick={applyBoundarySelections} variant="outline" disabled={assigning || !Object.keys(stagedBoundaries).length}>
                  {assigning ? 'Applying…' : `Apply ${Object.keys(stagedBoundaries).length || 0} staged boundaries`}
                </Button>
                <p className="text-xs text-amber-800">Use `Boundary` to queue real slate screenshots first. Then click `Apply staged boundaries` once. Use `Assign` for single product photos that still need manual attachment.</p>
              </div>
            </div>
            <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-5">
              {queue.unassigned_photos.map((photo) => (
                <UnassignedPhotoCard
                  key={photo.id}
                  photo={photo}
                  selected={selectedPhotoIds.includes(photo.id)}
                  boundaryTarget={stagedBoundaries[photo.id] || ''}
                  slateLabel={selectedSlateLabel}
                  assigning={assigning}
                  onToggle={toggleSelectedPhoto}
                  onMarkBoundary={markPhotoAsBoundary}
                  onAssignOne={assignSinglePhoto}
                />
              ))}
            </div>
          </SectionPanel>
        ) : null}

        <SectionPanel title="Item batches" description="The core grouping rule is simple: every photo after a slate belongs to that item until the next slate. Use these controls when the photo stream needs manual cleanup.">
          {loading ? (
            <p className="text-sm text-[var(--pp-muted)]">Loading intake batches…</p>
          ) : queue.batches?.length ? (
            <div className="space-y-4">
              {queue.batches.map((batch) => <BatchCard key={batch.id} batch={batch} onRefresh={load} />)}
            </div>
          ) : (
            <div className="rounded-[22px] border border-dashed border-[var(--pp-border)] bg-[var(--pp-surface-muted)] p-6 text-sm text-[var(--pp-muted)]">
              <div className="flex items-start gap-3">
                <ShieldAlert size={18} className="mt-0.5" />
                <div>
                  <p className="font-semibold text-[var(--pp-text)]">No batches yet</p>
                  <p className="mt-1">Start with a head slate, upload or sync the item photos, then come back here to review the grouped batch and generate the draft listing.</p>
                </div>
              </div>
            </div>
          )}
        </SectionPanel>
      </div>
    </AppShell>
  );
}
