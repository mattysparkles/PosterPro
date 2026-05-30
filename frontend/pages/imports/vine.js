import Link from 'next/link';
import { useRouter } from 'next/router';
import { useEffect, useMemo, useRef, useState } from 'react';
import toast from 'react-hot-toast';

import AppShell from '../../components/layout/AppShell';
import Button from '../../components/ui/button';
import DataTable from '../../components/ui/data-table';
import EmptyState from '../../components/ui/empty-state';
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
  toggleAutonomousMode,
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

export default function VineImportPage() {
  const router = useRouter();
  const { user } = useAuth();
  const { autonomousConfig, reload } = useDashboardData(user?.id);
  const fileInputRef = useRef(null);
  const [uploading, setUploading] = useState(false);
  const [busyAction, setBusyAction] = useState('');
  const [batches, setBatches] = useState([]);
  const [activeBatch, setActiveBatch] = useState(null);
  const [selectedIds, setSelectedIds] = useState([]);
  const [lastDraftListingIds, setLastDraftListingIds] = useState([]);

  const canAccess = !!user?.can_access_vine_import;

  const loadBatches = async (batchId = null) => {
    if (!canAccess) return;
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
  };

  useEffect(() => {
    if (!router.isReady) return;
    const requestedBatchId = Number.parseInt(String(router.query.batch || ''), 10);
    loadBatches(Number.isFinite(requestedBatchId) ? requestedBatchId : null).catch(() => undefined);
  }, [canAccess, router.isReady, router.query.batch]);

  const items = activeBatch?.items || [];
  const selectedItems = useMemo(() => items.filter((item) => selectedIds.includes(item.id)), [items, selectedIds]);
  const hasSelection = selectedIds.length > 0;

  const toggleRow = (id) => {
    setSelectedIds((current) => (current.includes(id) ? current.filter((value) => value !== id) : [...current, id]));
  };

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
        description="Import Amazon Vine itemized reports and create draft listings from eligible items."
        actions={
          <div className="flex gap-2">
            <Link href="/inventory">
              <Button variant="outline">Back to inventory</Button>
            </Link>
            <Button onClick={() => fileInputRef.current?.click()} disabled={uploading}>
              {uploading ? 'Uploading...' : 'Upload report'}
            </Button>
          </div>
        }
      />

      <SectionPanel title="Upload" description="Accepted formats: .xlsx and .csv preferred, .pdf supported with required preflight review.">
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
                toast.success('Vine report parsed.');
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

      <SectionPanel title="Preflight Review" description="Review parsed rows before creating inventory or drafts. Cancelled, locked, restricted, and low-confidence rows stay gated.">
        {activeBatch ? (
          <>
            <div className="mb-4 flex flex-wrap items-center gap-2">
              <StatusPill status="default" label={`${activeBatch.parsed_count} parsed`} />
              <StatusPill status="success" label={`${activeBatch.eligible_count} eligible`} />
              <StatusPill status="warning" label={`${activeBatch.locked_count} locked`} />
              <StatusPill status="danger" label={`${activeBatch.cancelled_count} cancelled`} />
              {activeBatch.source_type === 'pdf' ? <StatusPill status="warning" label="PDF requires review" /> : null}
            </div>

            <div className="mb-4 flex flex-wrap items-center gap-2 rounded-[12px] border border-[#e5e7eb] bg-white px-4 py-3">
                <span className="text-sm font-medium text-[#101828]">{selectedIds.length} selected</span>
                <Button size="sm" variant="outline" onClick={() => setSelectedIds(items.map((item) => item.id))} disabled={!items.length}>
                  Select all
                </Button>
                <Button size="sm" variant="outline" onClick={() => setSelectedIds([])} disabled={!hasSelection}>
                  Clear
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!!busyAction || !hasSelection}
                  onClick={async () => {
                    setBusyAction('media');
                    try {
                      const result = await fetchVineMedia(activeBatch.id, selectedIds);
                      await loadBatches(activeBatch.id);
                      const fetched = Number(result?.fetched || 0);
                      const blocked = Number(result?.blocked || 0);
                      if (fetched && !blocked) {
                        toast.success(`Image lookup finished (${fetched} fetched).`);
                      } else if (fetched && blocked) {
                        toast.success(`Image lookup finished (${fetched} fetched, ${blocked} blocked).`);
                      } else if (blocked) {
                        toast.error(
                          `Image lookup blocked for ${blocked} item(s). Enable AMAZON_MEDIA_LOOKUP_ENABLED and AMAZON_MEDIA_PAGE_FALLBACK_ENABLED, then retry.`,
                        );
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
                  disabled={!!busyAction || !hasSelection}
                  onClick={async () => {
                    const manualUrl = window.prompt('Paste Amazon product URL to use for selected rows');
                    if (!manualUrl) return;
                    setBusyAction('manual-url');
                    try {
                      await Promise.all(
                        selectedItems.map((item) =>
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
                  disabled={!!busyAction || !hasSelection}
                  onClick={async () => {
                    setBusyAction('retry-discovery');
                    try {
                      await Promise.all(selectedItems.map((item) => retryVineItemDiscovery(item.id)));
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
                  disabled={!!busyAction || !hasSelection}
                  onClick={async () => {
                    setBusyAction('inventory');
                    try {
                      await createVineInventory(activeBatch.id, selectedIds, true);
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
                  disabled={!!busyAction || !hasSelection}
                  onClick={async () => {
                    setBusyAction('drafts');
                    try {
                      const result = await createVineDrafts(activeBatch.id, selectedIds, {
                        fetchMediaFirst: true,
                        requireMediaForAsin: true,
                        allowDraftsWithoutMedia: false,
                      });
                      const listingIds = result?.listing_ids || result?.created_listing_ids || [];
                      setLastDraftListingIds(Array.isArray(listingIds) ? listingIds : []);
                      await loadBatches(activeBatch.id);
                      const created = Number(result?.created || 0);
                      const updated = Number(result?.updated || 0);
                      if (created || updated) {
                        toast.success(`Listing drafts ready (${created} created, ${updated} updated).`);
                      } else {
                        toast.success('Listing drafts ready.');
                      }
                    } catch (error) {
                      toast.error(error.message);
                    } finally {
                      setBusyAction('');
                    }
                  }}
                >
                  Fetch images + generate drafts
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
                      await Promise.all(selectedItems.map((item) => updateVineItem(item.id, { reviewed: true })));
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
                      <Link key={listingId} href={`/listings/${listingId}`}>
                        <Button size="sm" variant="outline">Open draft #{listingId}</Button>
                      </Link>
                    ))}
                    <Link href="/listings">
                      <Button size="sm" variant="outline">Open all listings</Button>
                    </Link>
                  </>
                ) : (
                  <Link href="/listings">
                    <Button size="sm" variant="outline">Open listings</Button>
                  </Link>
                )}
              </div>

            <DataTable
              columns={[
                { key: 'status', label: 'Status', render: (item) => <StatusPill status={item.eligibility_status.includes('locked') ? 'warning' : item.eligibility_status} label={item.eligibility_status.replace('locked_until_', 'Locked until ')} /> },
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
              rows={items}
              rowKey={(row) => row.id}
              selectedRows={selectedIds}
              onToggleRow={toggleRow}
              allSelected={items.length > 0 && selectedIds.length === items.length}
              onToggleAll={() => setSelectedIds(selectedIds.length === items.length ? [] : items.map((item) => item.id))}
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
