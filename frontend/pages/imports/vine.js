import Link from 'next/link';
import { useRouter } from 'next/router';
import { useEffect, useMemo, useRef, useState } from 'react';
import toast from 'react-hot-toast';

import AppShell from '../../components/layout/AppShell';
import ActionBar from '../../components/ui/action-bar';
import Button from '../../components/ui/button';
import DataTable from '../../components/ui/data-table';
import EmptyState from '../../components/ui/empty-state';
import ErrorState from '../../components/ui/error-state';
import LoadingSkeleton from '../../components/ui/loading-skeleton';
import PageHeader from '../../components/ui/page-header';
import SectionPanel from '../../components/ui/section-panel';
import StatusPill from '../../components/ui/status-pill';
import { useAuth } from '../../contexts/AuthContext';
import useDashboardData from '../../hooks/useDashboardData';
import {
  createVineDrafts,
  createVineInventory,
  fetchVineBatch,
  fetchVineBatches,
  fetchVineMedia,
  repairVineImages,
  toggleAutonomousMode,
  updateCurrentUser,
  updateVineItem,
  uploadVineReport,
  retryVineItemDiscovery,
} from '../../lib/api';

function formatDate(value) {
  if (!value) return '—';
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(value));
}

function buildSkippedCsv(items) {
  const rows = [
    ['id', 'product_name', 'asin', 'order_number', 'eligibility_status', 'warnings', 'restricted_reasons'],
    ...items
      .filter((item) => item.eligibility_status !== 'eligible' || item.restricted_review_required || (item.parse_warnings_json || []).length)
      .map((item) => [
        item.id,
        item.product_name || '',
        item.asin || '',
        item.order_number || '',
        item.eligibility_status,
        (item.parse_warnings_json || []).join('; '),
        (item.restricted_reasons || []).join('; '),
      ]),
  ];
  return rows.map((row) => row.map((value) => `"${String(value).replaceAll('"', '""')}"`).join(',')).join('\n');
}

function isDuplicateVineRow(item) {
  return (item.parse_warnings_json || []).some((warning) => String(warning).toLowerCase().startsWith('duplicate of prior vine import row'));
}

function getVineImageStatus(item) {
  if (isDuplicateVineRow(item)) return 'duplicate';
  if (item.media_status === 'cached' || item.media_status === 'fetched') return 'images';
  if (!item.media_status || item.media_status === 'pending') return 'needs_images';
  return String(item.media_status || 'pending');
}

export default function VineImportPage() {
  const router = useRouter();
  const { user, refreshUser } = useAuth();
  const { autonomousConfig, reload } = useDashboardData(user?.id);
  const fileInputRef = useRef(null);
  const [uploading, setUploading] = useState(false);
  const [busyAction, setBusyAction] = useState('');
  const [batches, setBatches] = useState([]);
  const [activeBatch, setActiveBatch] = useState(null);
  const [selectedIds, setSelectedIds] = useState([]);
  const [lastDraftListingIds, setLastDraftListingIds] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [enforceSixMonthLock, setEnforceSixMonthLock] = useState(true);
  const [draftProgress, setDraftProgress] = useState(null);
  const [filterMode, setFilterMode] = useState('all');

  const canAccess = !!user?.can_access_vine_import;

  useEffect(() => {
    setEnforceSixMonthLock(user?.vine_enforce_six_month_lock ?? true);
  }, [user?.vine_enforce_six_month_lock]);

  const loadBatches = async (batchId = null) => {
    if (!canAccess) return;
    setLoading(true);
    setLoadError('');
    try {
      const history = await fetchVineBatches();
      setBatches(history || []);
      const targetId = batchId || activeBatch?.id || history?.[0]?.id;
      if (targetId) {
        const details = await fetchVineBatch(targetId);
        setActiveBatch(details);
        setSelectedIds([]);
      } else {
        setActiveBatch(null);
      }
    } catch (error) {
      setLoadError(error.message || 'Failed to load Vine import history.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!router.isReady) return;
    const requestedBatchId = Number.parseInt(String(router.query.batch || ''), 10);
    loadBatches(Number.isFinite(requestedBatchId) ? requestedBatchId : null).catch(() => undefined);
  }, [canAccess, router.isReady, router.query.batch]);

  const items = activeBatch?.items || [];
  const hasSelection = selectedIds.length > 0;
  const duplicateCount = activeBatch?.stats_json?.rows_duplicate ?? items.filter(isDuplicateVineRow).length;
  const newCount = activeBatch?.stats_json?.rows_new ?? Math.max((activeBatch?.parsed_count || items.length) - duplicateCount, 0);
  const filteredItems = useMemo(() => {
    const normalizedFilter = String(filterMode || 'all');
    return items.filter((item) => {
      if (normalizedFilter === 'all') return true;
      if (normalizedFilter === 'new') return !isDuplicateVineRow(item);
      if (normalizedFilter === 'eligible') return item.eligibility_status === 'eligible' && !isDuplicateVineRow(item);
      if (normalizedFilter === 'locked') return String(item.eligibility_status || '').startsWith('locked_until_');
      if (normalizedFilter === 'cancelled') return item.eligibility_status === 'cancelled';
      if (normalizedFilter === 'duplicates') return isDuplicateVineRow(item);
      if (normalizedFilter === 'drafts_created') return !!item.listing_id;
      if (normalizedFilter === 'not_created') return !item.listing_id;
      if (normalizedFilter === 'needs_images') return getVineImageStatus(item) === 'needs_images';
      return true;
    });
  }, [filterMode, items]);
  const filteredSelection = useMemo(() => filteredItems.filter((item) => selectedIds.includes(item.id)), [filteredItems, selectedIds]);
  const selectedFilteredIds = filteredSelection.map((item) => item.id);

  const toggleRow = (id) => {
    setSelectedIds((current) => (current.includes(id) ? current.filter((value) => value !== id) : [...current, id]));
  };

  useEffect(() => {
    setSelectedIds((current) => current.filter((id) => filteredItems.some((item) => item.id === id)));
  }, [filteredItems]);

  const exportSkippedRows = () => {
    const csv = buildSkippedCsv(items);
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = href;
    anchor.download = `vine-import-${activeBatch?.id || 'rows'}.csv`;
    anchor.click();
    URL.revokeObjectURL(href);
  };

  if (!canAccess) {
    return (
      <AppShell active="/imports/vine" title="Vine Import" autonomousConfig={autonomousConfig} onToggleAutonomous={async () => {
        await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
        await reload();
      }}>
        <EmptyState title="Access restricted" description="Amazon Vine import is only available to approved internal users when the feature flag is enabled." />
      </AppShell>
    );
  }

  return (
    <AppShell
      active="/imports/vine"
      title="Amazon Vine Import"
      autonomousConfig={autonomousConfig}
      onToggleAutonomous={async () => {
        await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
        await reload();
      }}
    >
      <PageHeader
        title="Amazon Vine Import"
        description="Import Amazon Vine itemized reports and create robust draft listings, including rows marked locked/cancelled when needed."
        actions={
          <div className="flex gap-2">
            <Button href="/inventory" variant="outline">
              Back to inventory
            </Button>
            <Button onClick={() => fileInputRef.current?.click()} disabled={uploading}>
              {uploading ? 'Uploading...' : 'Upload report'}
            </Button>
          </div>
        }
      />

      <SectionPanel title="Upload" description="Accepted formats: .xlsx and .csv preferred, .pdf supported with required preflight review.">
        <div className="mb-3 rounded-[12px] border border-[#e4e7ec] bg-[#f9fafb] px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-[#101828]">6-month eligibility lock</p>
              <p className="mt-1 text-xs text-[#667085]">
                Keep this on to enforce lock windows. Turn it off to mark rows eligible by default while still recording and showing each row&apos;s &quot;Eligible After&quot; date.
              </p>
            </div>
            <label className="inline-flex items-center gap-2 text-sm font-medium text-[#344054]">
              <input
                type="checkbox"
                checked={!!enforceSixMonthLock}
                onChange={async (event) => {
                  const nextValue = event.target.checked;
                  setEnforceSixMonthLock(nextValue);
                  try {
                    await updateCurrentUser({ vine_enforce_six_month_lock: nextValue });
                    await refreshUser();
                    toast.success(nextValue ? '6-month lock enforcement enabled.' : '6-month lock enforcement disabled.');
                  } catch (error) {
                    setEnforceSixMonthLock((user?.vine_enforce_six_month_lock ?? true));
                    toast.error(error.message || 'Failed to update Vine lock preference.');
                  }
                }}
              />
              Enforce 6-month lock
            </label>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3 rounded-[12px] border border-dashed border-[#d0d5dd] bg-white px-4 py-5">
          <input
            ref={fileInputRef}
            type="file"
            accept=".xlsx,.csv,.pdf"
            className="hidden"
            onChange={async (event) => {
              const file = event.target.files?.[0];
              if (!file) return;
              setUploading(true);
              try {
              const batch = await uploadVineReport(file);
              setActiveBatch(batch);
              setSelectedIds([]);
              await loadBatches(batch.id);
              const duplicateRows = Number(batch?.stats_json?.rows_duplicate || 0);
              const newRows = Number(batch?.stats_json?.rows_new || 0);
              toast.success(
                duplicateRows
                  ? `Vine report parsed (${newRows} new, ${duplicateRows} duplicate row${duplicateRows === 1 ? '' : 's'} ignored).`
                  : 'Vine report parsed.',
              );
            } catch (error) {
              toast.error(error.message);
            } finally {
              setUploading(false);
              event.target.value = '';
              }
            }}
          />
          <div>
            <p className="text-sm font-medium text-[#101828]">Upload Amazon Vine itemized report</p>
            <p className="mt-1 text-sm text-[#667085]">Use XLSX or CSV when possible. PDF imports stay in manual-review mode until you verify the parse.</p>
          </div>
        </div>
      </SectionPanel>
      {loadError ? <ErrorState title="Could not load Vine imports" description={loadError} action={<Button variant="outline" onClick={() => loadBatches(activeBatch?.id || null)}>Retry</Button>} /> : null}
      {loading ? <LoadingSkeleton lines={4} className="mb-4" /> : null}

      <SectionPanel title="Import History" description="Recent private Vine imports for this account.">
        <DataTable
          columns={[
            { key: 'filename', label: 'Filename' },
            { key: 'uploaded', label: 'Uploaded', render: (row) => formatDate(row.created_at) },
            { key: 'parsed', label: 'Parsed rows', render: (row) => row.parsed_count },
            { key: 'eligible', label: 'Eligible', render: (row) => row.eligible_count },
            { key: 'locked', label: 'Locked', render: (row) => row.locked_count },
            { key: 'cancelled', label: 'Cancelled', render: (row) => row.cancelled_count },
            { key: 'drafts', label: 'Drafts created', render: (row) => row.drafts_created_count || 0 },
            { key: 'status', label: 'Status', render: (row) => <StatusPill status={row.status} label={row.status} /> },
            {
              key: 'open',
              label: 'Open',
              render: (row) => (
                <Button variant="outline" size="sm" onClick={async () => setActiveBatch(await fetchVineBatch(row.id))}>
                  View
                </Button>
              ),
            },
          ]}
          rows={batches}
          rowKey={(row) => row.id}
          emptyState={<EmptyState title="No imports yet" description="Upload your first Vine report to begin the preflight review." className="border-0 p-0 py-6" />}
        />
      </SectionPanel>

      <SectionPanel title="Preflight Review" description="Review parsed rows before creating inventory or drafts. For this workflow, locked/cancelled rows can still be drafted so nothing gets stranded.">
        {activeBatch ? (
          <>
            {!enforceSixMonthLock ? (
              <div className="mb-3 rounded-[10px] border border-[#fec84b] bg-[#fffaeb] px-3 py-2 text-xs text-[#7a2e0e]">
                Lock enforcement is currently off for your account. Rows are marked eligible by default; review each row&apos;s <strong>Eligible After</strong> date before posting.
              </div>
            ) : null}
            <div className="mb-4 flex flex-wrap items-center gap-2">
              <Button size="sm" variant={filterMode === 'all' ? 'default' : 'outline'} onClick={() => setFilterMode('all')}>
                All ({items.length})
              </Button>
              <Button size="sm" variant={filterMode === 'new' ? 'default' : 'outline'} onClick={() => setFilterMode('new')}>
                New ({newCount})
              </Button>
              <Button size="sm" variant={filterMode === 'eligible' ? 'default' : 'outline'} onClick={() => setFilterMode('eligible')}>
                Eligible ({activeBatch.eligible_count})
              </Button>
              <Button size="sm" variant={filterMode === 'locked' ? 'default' : 'outline'} onClick={() => setFilterMode('locked')}>
                Locked ({activeBatch.locked_count})
              </Button>
              <Button size="sm" variant={filterMode === 'cancelled' ? 'default' : 'outline'} onClick={() => setFilterMode('cancelled')}>
                Cancelled ({activeBatch.cancelled_count})
              </Button>
              <Button size="sm" variant={filterMode === 'duplicates' ? 'default' : 'outline'} onClick={() => setFilterMode('duplicates')}>
                Duplicates ignored ({duplicateCount})
              </Button>
              <Button size="sm" variant={filterMode === 'drafts_created' ? 'default' : 'outline'} onClick={() => setFilterMode('drafts_created')}>
                Drafts created ({items.filter((item) => item.listing_id).length})
              </Button>
              <Button size="sm" variant={filterMode === 'needs_images' ? 'default' : 'outline'} onClick={() => setFilterMode('needs_images')}>
                Needs images ({items.filter((item) => getVineImageStatus(item) === 'needs_images').length})
              </Button>
              {activeBatch.source_type === 'pdf' ? <StatusPill status="warning" label="PDF requires review" /> : null}
            </div>
            <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-[#667085]">
              <span className="font-medium text-[#344054]">Showing {filteredItems.length} of {items.length} rows</span>
              {filterMode !== 'all' ? (
                <Button size="sm" variant="ghost" onClick={() => setFilterMode('all')}>
                  Clear filter
                </Button>
              ) : null}
            </div>

            <div className="mb-4 flex flex-wrap items-center gap-2 rounded-[12px] border border-[#e5e7eb] bg-white px-4 py-3">
                <span className="text-sm font-medium text-[#101828]">{selectedIds.length} selected</span>
                <Button size="sm" variant="outline" onClick={() => setSelectedIds(filteredItems.map((item) => item.id))} disabled={!filteredItems.length}>
                  Select all
                </Button>
                <Button size="sm" variant="outline" onClick={() => setSelectedIds([])} disabled={!hasSelection}>
                  Clear
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!!busyAction || !selectedFilteredIds.length}
                  onClick={async () => {
                    setBusyAction('media');
                    try {
                      const result = await fetchVineMedia(activeBatch.id, selectedFilteredIds);
                      await loadBatches(activeBatch.id);
                      const fetched = Number(result?.fetched || 0);
                      const blocked = Number(result?.blocked || 0);
                      if (fetched && !blocked) {
                        toast.success(`Image lookup finished (${fetched} fetched).`);
                      } else if (fetched && blocked) {
                        toast.success(`Image lookup finished (${fetched} fetched, ${blocked} blocked).`);
                      } else if (blocked) {
                        toast.error(`Image lookup blocked for ${blocked} item(s). Check the Amazon ASIN, manual URL, or bridge/session availability, then retry.`);
                      } else {
                        toast.success('Image lookup finished.');
                      }
                    } catch (error) {
                      toast.error(error.message);
                    } finally {
                      setBusyAction('');
                    }
                  }}
                >
                  Fetch Amazon images
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!!busyAction || !selectedFilteredIds.length}
                  onClick={async () => {
                    setBusyAction('repair-images');
                    try {
                      const result = await repairVineImages(activeBatch.id, selectedFilteredIds);
                      await loadBatches(activeBatch.id);
                      const updated = Number(result?.updated || 0);
                      const removedUnsafe = Number(result?.removed_unsafe || 0);
                      toast.success(
                        removedUnsafe || updated
                          ? `Image repair finished (${updated} listings refreshed, ${removedUnsafe} unsafe image(s) removed).`
                          : 'Image repair finished.',
                      );
                    } catch (error) {
                      toast.error(error.message);
                    } finally {
                      setBusyAction('');
                    }
                  }}
                >
                  Repair Amazon images
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!!busyAction || !selectedFilteredIds.length}
                  onClick={async () => {
                    const manualUrl = window.prompt('Paste Amazon product URL to use for selected rows');
                    if (!manualUrl) return;
                    setBusyAction('manual-url');
                    try {
                      await Promise.all(
                        filteredSelection.map((item) =>
                          updateVineItem(item.id, { manual_amazon_url: manualUrl.trim() }).then(() => retryVineItemDiscovery(item.id)),
                        ),
                      );
                      await loadBatches(activeBatch.id);
                      toast.success('Manual Amazon URL saved and discovery retried.');
                    } catch (error) {
                      toast.error(error.message);
                    } finally {
                      setBusyAction('');
                    }
                  }}
                >
                  Set manual Amazon URL + retry
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!!busyAction || !selectedFilteredIds.length}
                  onClick={async () => {
                    setBusyAction('retry-discovery');
                    try {
                      await Promise.all(filteredSelection.map((item) => retryVineItemDiscovery(item.id)));
                      await loadBatches(activeBatch.id);
                      toast.success('Discovery retried for selected rows.');
                    } catch (error) {
                      toast.error(error.message);
                    } finally {
                      setBusyAction('');
                    }
                  }}
                >
                  Retry match/image discovery
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!!busyAction || !selectedFilteredIds.length}
                  onClick={async () => {
                    setBusyAction('inventory');
                    try {
                      await createVineInventory(activeBatch.id, selectedFilteredIds, true, true);
                      await loadBatches(activeBatch.id);
                      toast.success('Inventory records created.');
                    } catch (error) {
                      toast.error(error.message);
                    } finally {
                      setBusyAction('');
                    }
                  }}
                >
                  Create inventory records
                </Button>
                <Button
                  size="sm"
                  disabled={!!busyAction || !selectedFilteredIds.length}
                  onClick={async () => {
                    setBusyAction('drafts');
                    try {
                      const chunkSize = 5;
                      const chunks = [];
                      for (let index = 0; index < selectedFilteredIds.length; index += chunkSize) {
                        chunks.push(selectedFilteredIds.slice(index, index + chunkSize));
                      }

                      let totalCreated = 0;
                      let totalUpdated = 0;
                      let totalSkipped = 0;
                      let combinedListingIds = [];

                      for (let index = 0; index < chunks.length; index += 1) {
                        const chunk = chunks[index];
                        setDraftProgress({ current: index + 1, total: chunks.length });
                        const result = await createVineDrafts(activeBatch.id, chunk, {
                          fetchMediaFirst: false,
                          requireMediaForAsin: false,
                          allowDraftsWithoutMedia: true,
                          includeCancelled: true,
                        });
                        totalCreated += Number(result?.created || 0);
                        totalUpdated += Number(result?.updated || 0);
                        totalSkipped += Number(result?.skipped || 0);
                        const listingIds = result?.listing_ids || result?.created_listing_ids || [];
                        if (Array.isArray(listingIds) && listingIds.length) {
                          combinedListingIds = [...combinedListingIds, ...listingIds];
                        }
                      }

                      setLastDraftListingIds(combinedListingIds);
                      await loadBatches(activeBatch.id);
                      toast.success(`Listing drafts ready (${totalCreated} created, ${totalUpdated} updated, ${totalSkipped} skipped).`);
                    } catch (error) {
                      toast.error(error.message);
                    } finally {
                      setDraftProgress(null);
                      setBusyAction('');
                    }
                  }}
                >
                  {busyAction === 'drafts' && draftProgress
                    ? `Generating drafts ${draftProgress.current}/${draftProgress.total}...`
                    : 'Fetch images + generate drafts'}
                </Button>
                <Button size="sm" variant="outline" onClick={exportSkippedRows}>
                  Export skipped/error rows
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!!busyAction || !hasSelection}
                  onClick={async () => {
                    setBusyAction('reviewed');
                    try {
                      await Promise.all(filteredSelection.map((item) => updateVineItem(item.id, { reviewed: true })));
                      await loadBatches(activeBatch.id);
                      toast.success('Marked as reviewed.');
                    } catch (error) {
                      toast.error(error.message);
                    } finally {
                      setBusyAction('');
                    }
                  }}
                >
                  Mark as reviewed
                </Button>
                {lastDraftListingIds.length ? (
                  <>
                    {lastDraftListingIds.slice(0, 6).map((listingId) => (
                      <Button key={listingId} href={`/listings/${listingId}`} size="sm" variant="outline">
                        Open draft #{listingId}
                      </Button>
                    ))}
                    <Button href="/listings" size="sm" variant="outline">
                      Open all listings
                    </Button>
                  </>
                ) : (
                  <Button href="/listings" size="sm" variant="outline">
                    Open listings
                  </Button>
                )}
              </div>

            <DataTable
              columns={[
                { key: 'status', label: 'Status', render: (item) => <StatusPill status={item.eligibility_status.includes('locked') ? 'warning' : item.eligibility_status} label={item.eligibility_status.replace('locked_until_', 'Locked until ')} /> },
                { key: 'import_state', label: 'Import State', render: (item) => isDuplicateVineRow(item) ? <StatusPill status="default" label="Duplicate" /> : <StatusPill status="success" label="New" /> },
                { key: 'product_name', label: 'Product Name', cellClassName: 'min-w-[240px]', render: (item) => <div><p className="font-medium text-[#101828]">{item.product_name || 'Untitled item'}</p>{item.restricted_review_required ? <p className="mt-1 text-xs text-[#b42318]">{(item.restricted_reasons || []).join(', ')}</p> : null}</div> },
                { key: 'asin', label: 'ASIN' },
                { key: 'brand', label: 'Brand' },
                { key: 'category', label: 'Category' },
                { key: 'order_number', label: 'Order Number' },
                { key: 'order_type', label: 'Order Type' },
                { key: 'order_date', label: 'Order Date', render: (item) => formatDate(item.order_date) },
                { key: 'shipped_date', label: 'Shipped Date', render: (item) => formatDate(item.shipped_date) },
                { key: 'cancelled_date', label: 'Cancelled Date', render: (item) => formatDate(item.cancelled_date) },
                { key: 'estimated_tax_value', label: 'Estimated Tax Value', render: (item) => item.estimated_tax_value == null ? '—' : `$${Number(item.estimated_tax_value).toFixed(2)}` },
                { key: 'eligible_after', label: 'Eligible After', render: (item) => formatDate(item.eligible_after) },
                { key: 'match_status', label: 'Amazon Match', render: (item) => <StatusPill status={item.amazon_match_status || 'default'} label={item.amazon_match_status || 'pending'} /> },
                { key: 'image_status', label: 'Image Status', render: (item) => <StatusPill status={item.media_status || 'default'} label={item.media_status || 'pending'} /> },
                { key: 'draft_status', label: 'Draft Status', render: (item) => item.listing_id ? <StatusPill status="success" label="Created" /> : <StatusPill status="default" label="Not created" /> },
                { key: 'manual_amazon_url', label: 'Manual URL', render: (item) => item.manual_amazon_url ? 'Set' : '—' },
              ]}
              rows={filteredItems}
              rowKey={(row) => row.id}
              selectedRows={selectedIds}
              onToggleRow={toggleRow}
              allSelected={filteredItems.length > 0 && selectedIds.length === filteredItems.length}
              onToggleAll={() => setSelectedIds(selectedIds.length === filteredItems.length ? [] : filteredItems.map((item) => item.id))}
              emptyState={<EmptyState title="No parsed rows" description="Upload a Vine report to populate the preflight review table." className="border-0 p-0 py-6" />}
            />
          </>
        ) : (
          <EmptyState title="No active batch" description="Select an import from history or upload a new report to review parsed Vine rows." className="border-0 p-0 py-6" />
        )}
      </SectionPanel>
    </AppShell>
  );
}

VineImportPage.requireAuth = true;
