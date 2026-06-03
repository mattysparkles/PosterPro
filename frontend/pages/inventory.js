import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { ChevronDown, PencilLine, RefreshCcw, Search, Tag, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { useRouter } from 'next/router';

import AppShell from '../components/layout/AppShell';
import ActionBar from '../components/ui/action-bar';
import PhotoEditorModal from '../components/PhotoEditorModal';
import Button from '../components/ui/button';
import DataTable from '../components/ui/data-table';
import DataTableRowAction from '../components/ui/data-table-row-action';
import EmptyState from '../components/ui/empty-state';
import { Tabs } from '../components/ui/tabs';
import Input from '../components/ui/input';
import MetricCard from '../components/ui/metric-card';
import PageHeader from '../components/ui/page-header';
import SectionPanel from '../components/ui/section-panel';
import StatusPill from '../components/ui/status-pill';
import Toolbar from '../components/ui/toolbar';
import { useAuth } from '../contexts/AuthContext';
import useDashboardData from '../hooks/useDashboardData';
import { fetchInventory, processListingPhoto, runInventoryBulkJob, toggleAutonomousMode, toPublicImageUrl } from '../lib/api';

const INVENTORY_TABS = [
  { value: 'active', label: 'Active' },
  { value: 'intake', label: 'Intake' },
  { value: 'sold', label: 'Sold' },
  { value: 'stale', label: 'Stale' },
  { value: 'multi', label: 'Multi-quantity' },
  { value: 'batches', label: 'Batches' },
];

const FILTER_OPTIONS = [
  { value: 'all', label: 'All items' },
  { value: 'multi', label: 'Qty > 1' },
  { value: 'stale', label: 'Stale inventory' },
];

function getInventoryStatus(item) {
  if (item.quantity <= 0) return { label: 'Sold', tone: 'danger' };
  if (item.status === 'archived') return { label: 'Archived', tone: 'default' };
  if (item.status === 'ingested' || item.status === 'INGESTED') return { label: 'Intake', tone: 'info' };
  return { label: 'Active', tone: 'success' };
}

function getBatchLabel(item, storageBatches) {
  const batch = storageBatches.find((row) => row.id === item.batch_id);
  return batch?.name || (item.batch_id ? `Batch ${item.batch_id}` : 'Unassigned');
}

function inferIsStale(item) {
  if (typeof item?.is_stale === 'boolean') return item.is_stale;
  const latest = item?.last_synced_at || item?.updated_at || item?.listed_at || item?.created_at;
  if (!latest) return false;
  const ts = new Date(latest).getTime();
  if (Number.isNaN(ts)) return false;
  const ageDays = (Date.now() - ts) / (1000 * 60 * 60 * 24);
  return ageDays >= 30;
}

export default function InventoryPage() {
  const { user } = useAuth();
  const router = useRouter();
  const { autonomousConfig, reload, storageBatches, clusters } = useDashboardData(user?.id);
  const [activeTab, setActiveTab] = useState('active');
  const [search, setSearch] = useState('');
  const [inventory, setInventory] = useState([]);
  const [selectedIds, setSelectedIds] = useState([]);
  const [filter, setFilter] = useState('all');
  const [isLoading, setIsLoading] = useState(false);
  const [editingListing, setEditingListing] = useState(null);

  const loadInventory = useCallback(async () => {
    setIsLoading(true);
    try {
      const response = await fetchInventory({
        search,
        page: 1,
        pageSize: 200,
        quantityGtOne: filter === 'multi',
        stale: filter === 'stale',
      });
      setInventory(response.items || []);
    } finally {
      setIsLoading(false);
    }
  }, [filter, search]);

  useEffect(() => {
    loadInventory();
  }, [loadInventory]);

  useEffect(() => {
    if (!router.isReady) return;
    const requestedTab = typeof router.query.tab === 'string' ? router.query.tab : 'active';
    if (INVENTORY_TABS.some((tab) => tab.value === requestedTab)) {
      setActiveTab(requestedTab);
    }
  }, [router.isReady, router.query.tab]);

  const selectTab = (nextTab) => {
    setActiveTab(nextTab);
    setSelectedIds([]);
    if (!router.isReady) return;
    router.replace({ pathname: router.pathname, query: { ...router.query, tab: nextTab } }, undefined, { shallow: true });
  };

  const filteredInventory = useMemo(() => {
    if (activeTab === 'multi') return inventory.filter((item) => Number(item.quantity || 0) > 1);
    if (activeTab === 'stale') return inventory.filter((item) => inferIsStale(item));
    if (activeTab === 'sold') return inventory.filter((item) => item.quantity <= 0);
    if (activeTab === 'intake') return inventory.filter((item) => item.status === 'ingested' || item.status === 'INGESTED');
    if (activeTab === 'active') {
      return inventory.filter((item) => item.quantity > 0 && item.status !== 'archived' && item.status !== 'ingested' && item.status !== 'INGESTED');
    }
    return [];
  }, [activeTab, inventory]);

  const tabCounts = useMemo(
    () => ({
      intake: inventory.filter((item) => item.status === 'ingested' || item.status === 'INGESTED').length,
      active: inventory.filter((item) => item.quantity > 0 && item.status !== 'archived' && item.status !== 'ingested' && item.status !== 'INGESTED').length,
      sold: inventory.filter((item) => item.quantity <= 0).length,
      stale: inventory.filter((item) => inferIsStale(item)).length,
      multi: inventory.filter((item) => Number(item.quantity || 0) > 1).length,
      batches: storageBatches.length || clusters.length,
    }),
    [clusters.length, inventory, storageBatches.length],
  );

  const toggleRow = (id) => {
    setSelectedIds((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  };

  const runBulkAction = async (action) => {
    if (!selectedIds.length) {
      toast.error('Select inventory items first.');
      return;
    }
    await runInventoryBulkJob({ action, listing_ids: selectedIds, filters: {}, payload: {} });
    toast.success(`Queued ${action} for ${selectedIds.length} items.`);
    setSelectedIds([]);
    await loadInventory();
  };

  const batches = storageBatches.length
    ? storageBatches.map((batch) => ({
        id: batch.id,
        name: batch.name || `Batch ${batch.id}`,
        item_count: batch.item_count || 0,
        photo_count: batch.photo_count || batch.image_count || 0,
        status: batch.status || 'Queued',
      }))
    : clusters.map((cluster) => ({
        id: cluster.id,
        name: `Batch ${cluster.id}`,
        item_count: cluster.listing_count || 0,
        photo_count: cluster.image_count || 0,
        status: 'Grouped',
      }));
  const inventoryMetrics = useMemo(
    () => [
      { label: 'Intake rows', value: tabCounts.intake, detail: 'Photos and fresh items still early in the pipeline.' },
      { label: 'Active inventory', value: tabCounts.active, detail: 'Current unsold listings and tracked items.' },
      { label: 'Sold items', value: tabCounts.sold, detail: 'Inventory rows already marked sold or depleted.' },
      { label: 'Batches', value: tabCounts.batches, detail: 'Batch containers or grouped clusters in the system.' },
    ],
    [tabCounts],
  );

  const inventorySubnav = useMemo(
    () => ({
      eyebrow: 'Inventory CMS',
      title: 'Inventory Control',
      description: 'Use a dedicated inventory rail for stock state, batch tracking, and bulk operations.',
      sections: [
        {
          label: 'Inventory Views',
          items: INVENTORY_TABS.map((tab) => ({
            key: tab.value,
            label: tab.label,
            active: activeTab === tab.value,
            badge: tabCounts[tab.value] || 0,
            description:
              tab.value === 'intake'
                ? 'Fresh intake-side rows'
                : tab.value === 'active'
                ? 'Available inventory'
                : tab.value === 'sold'
                ? 'Depleted or sold inventory'
                : 'Storage units and grouped batches',
            onClick: () => selectTab(tab.value),
          })),
        },
        {
          label: 'Sections',
          items: [
            { key: 'inventory-workflow', label: 'Workflow', active: false, description: 'Inventory lifecycle guide', onClick: () => document.getElementById('inventory-workflow')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) },
            { key: 'bulk-actions', label: 'Bulk Actions', active: false, description: 'Selection-based operations', onClick: () => document.getElementById('bulk-actions')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) },
            { key: 'inventory-table', label: 'Inventory Table', active: false, description: 'Current table view', onClick: () => document.getElementById('inventory-table')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) },
          ],
        },
      ],
    }),
    [activeTab, tabCounts],
  );

  return (
    <AppShell
      active="/inventory"
      title="Inventory"
      autonomousConfig={autonomousConfig}
      onToggleAutonomous={async () => {
        await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
        await reload();
      }}
      subnav={inventorySubnav}
    >
      <PageHeader
        title="Inventory"
        description="Track intake, active inventory, sold items, and batches."
        actions={
          user?.can_access_vine_import ? (
            <Link href="/imports/vine">
              <Button>Import Vine report</Button>
            </Link>
          ) : null
        }
      />

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {inventoryMetrics.map((card) => (
          <MetricCard key={card.label} label={card.label} value={card.value} detail={card.detail} />
        ))}
      </section>

      <section id="inventory-workflow" className="grid gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(320px,0.9fr)]">
        <SectionPanel title="Inventory workflow" description="How this workspace should move inventory through the system cleanly.">
          <div className="grid gap-3 md:grid-cols-4">
            {[
              ['1. Intake', 'Import photos and label the storage unit or batch source.'],
              ['2. Group', 'Let PosterPro cluster images into item candidates and batches.'],
              ['3. Review', 'Fix item metadata, quantities, and images before publish.'],
              ['4. Maintain', 'Track active, stale, and sold inventory over time.'],
            ].map(([title, detail]) => (
              <div key={title} className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">{title}</p>
                <p className="mt-2 text-sm text-[#667085]">{detail}</p>
              </div>
            ))}
          </div>
        </SectionPanel>
        <SectionPanel id="bulk-actions" title="Bulk actions" description="Use selection-based actions carefully so inventory stays trustworthy.">
          <div className="space-y-3">
            {[
              'Labeling is best for consistent operator review queues and storage-unit tracking.',
              'Delist should be used only when a listing is actually leaving marketplace circulation.',
              'Mark sold should follow a confirmed marketplace sale or manual local sale event.',
            ].map((item) => (
              <div key={item} className="rounded-[12px] border border-[#e5e7eb] bg-white p-4 text-sm text-[#667085]">
                {item}
              </div>
            ))}
          </div>
        </SectionPanel>
      </section>

      <Toolbar
        left={
          <>
            <div className="relative w-full sm:max-w-[320px]">
              <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[#98a2b3]" size={16} />
              <Input
                placeholder="Search inventory"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                className="w-full pl-9"
              />
            </div>
            <div className="relative w-full sm:w-[220px]">
              <select
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
                className="pp-input h-10 w-full appearance-none rounded-[10px] border border-[#e5e7eb] bg-white px-3 pr-10 text-sm text-[#101828] outline-none focus:border-[#2563eb] focus:ring-4 focus:ring-[#2563eb]/12"
              >
                {FILTER_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              <ChevronDown size={16} className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[#98a2b3]" />
            </div>
            <Button variant="outline" onClick={loadInventory}>
              <RefreshCcw size={16} />
              Refresh
            </Button>
          </>
        }
        right={<span>{isLoading ? 'Loading…' : activeTab === 'batches' ? `${batches.length} visible` : `${filteredInventory.length} visible`}</span>}
      />

      <Tabs
        items={INVENTORY_TABS.map((tab) => ({ ...tab, count: tabCounts[tab.value] || 0 }))}
        value={activeTab}
        onChange={selectTab}
      />

      {selectedIds.length && activeTab !== 'batches' ? (
        <ActionBar
          left={<p className="text-sm font-medium text-[#101828]">{selectedIds.length} selected</p>}
          right={
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" size="sm" onClick={() => setSelectedIds([])}>
                Clear
              </Button>
              <Button variant="outline" size="sm" onClick={() => runBulkAction('label')}>
                <Tag size={14} />
                Label
              </Button>
              <Button variant="outline" size="sm" onClick={() => runBulkAction('delist')}>
                <Trash2 size={14} />
                Delist
              </Button>
              <Button size="sm" onClick={() => runBulkAction('mark_sold')}>
                Mark sold
              </Button>
            </div>
          }
        />
      ) : null}

      <div id="inventory-table">
      {activeTab === 'batches' ? (
        <DataTable
          columns={[
            { key: 'name', label: 'Batch' },
            { key: 'item_count', label: 'Items' },
            { key: 'photo_count', label: 'Photos' },
            { key: 'status', label: 'Status', render: (batch) => <StatusPill status={String(batch.status).toLowerCase()} label={batch.status} /> },
          ]}
          rows={batches}
          rowKey={(row) => row.id}
          emptyState={<EmptyState title="No batches yet" description="New intake batches will appear here once photos are grouped." className="border-0 p-0 py-6" />}
        />
      ) : (
        <DataTable
          columns={[
            {
              key: 'item',
              label: 'Item',
              cellClassName: 'min-w-[240px]',
              render: (item) => (
                <div className="flex items-center gap-3">
                  <div className="h-10 w-10 overflow-hidden rounded-[10px] bg-[#f2f4f7]">
                    {item.image_urls?.[0] ? (
                      <img src={toPublicImageUrl(item.image_urls[0])} alt={item.title || `Item ${item.id}`} className="h-full w-full object-cover" />
                    ) : null}
                  </div>
                  <div>
                    <p className="truncate font-medium text-[#101828]">{item.title || `Listing #${item.id}`}</p>
                    <p className="mt-1 text-xs text-[#667085]">#{item.id}</p>
                  </div>
                </div>
              ),
            },
            { key: 'photos', label: 'Photos', render: (item) => item.image_urls?.length || 0 },
            { key: 'quantity', label: 'Qty', render: (item) => Number(item.quantity || 0) },
            {
              key: 'status',
              label: 'Status',
              render: (item) => {
                const status = getInventoryStatus(item);
                return <StatusPill status={status.label.toLowerCase()} label={status.label} />;
              },
            },
            { key: 'stale', label: 'Stale', render: (item) => <StatusPill status={inferIsStale(item) ? 'warning' : 'success'} label={inferIsStale(item) ? 'Stale' : 'Fresh'} /> },
            { key: 'batch', label: 'Batch', render: (item) => getBatchLabel(item, storageBatches) },
            { key: 'value', label: 'Value', render: (item) => `$${Number(item.price || item.suggested_price || 0).toFixed(0)}` },
            {
              key: 'actions',
              label: 'Actions',
              render: (item) => (
                <DataTableRowAction variant="outline" onClick={() => setEditingListing(item)}>
                  <PencilLine size={14} />
                  Edit photo
                </DataTableRowAction>
              ),
            },
          ]}
          rows={filteredInventory}
          rowKey={(row) => row.id}
          selectedRows={selectedIds}
          onToggleRow={toggleRow}
          allSelected={filteredInventory.length > 0 && selectedIds.length === filteredInventory.length}
          onToggleAll={() => setSelectedIds(selectedIds.length === filteredInventory.length ? [] : filteredInventory.map((item) => item.id))}
          emptyState={<EmptyState title="No inventory records" description="This tab does not have any matching inventory items yet." className="border-0 p-0 py-6" />}
        />
      )}
      </div>

      <PhotoEditorModal
        open={!!editingListing}
        listing={editingListing}
        onClose={() => setEditingListing(null)}
        onApply={async ({ listingId, sourceImage, file, removeBackground, edits }) => {
          await processListingPhoto({ listingId, sourceImage, file, removeBackground, edits });
          await loadInventory();
          await reload();
        }}
      />
    </AppShell>
  );
}

InventoryPage.requireAuth = true;
