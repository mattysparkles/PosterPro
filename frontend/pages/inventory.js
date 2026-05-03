import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { ChevronDown, PencilLine, RefreshCcw, Tag, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';

import AppShell from '../components/layout/AppShell';
import PhotoEditorModal from '../components/PhotoEditorModal';
import Badge from '../components/ui/badge';
import Button from '../components/ui/button';
import Input from '../components/ui/input';
import { Tabs } from '../components/ui/tabs';
import { useAuth } from '../contexts/AuthContext';
import useDashboardData from '../hooks/useDashboardData';
import { fetchInventory, processListingPhoto, runInventoryBulkJob, toggleAutonomousMode, toPublicImageUrl } from '../lib/api';

const INVENTORY_TABS = [
  { value: 'intake', label: 'Intake' },
  { value: 'active', label: 'Active' },
  { value: 'sold', label: 'Sold' },
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

export default function InventoryPage({ theme, setTheme }) {
  const { user } = useAuth();
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

  const filteredInventory = useMemo(() => {
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

  return (
    <AppShell
      active="/inventory"
      autonomousConfig={autonomousConfig}
      onToggleAutonomous={async () => {
        await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
        await reload();
      }}
      theme={theme}
      onToggleTheme={() => {
        const next = theme === 'dark' ? 'light' : 'dark';
        setTheme(next);
        localStorage.setItem('posterpro-theme', next);
        document.documentElement.classList.toggle('dark', next === 'dark');
      }}
    >
      <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
        <div>
          <p className="text-sm font-semibold text-[#667085]">Inventory</p>
          <h1 className="mt-1 text-[2rem] font-semibold tracking-[-0.04em] text-[#111827]">Inventory</h1>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={loadInventory}>
            <RefreshCcw size={16} />
            Refresh
          </Button>
          <Link href="/listings">
            <Button variant="outline">Open listings</Button>
          </Link>
        </div>
      </div>

      <div className="rounded-[18px] border border-[#e5e7eb] bg-white p-4">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
          <div className="flex flex-1 flex-col gap-3 sm:flex-row">
            <Input
              placeholder="Search inventory"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              className="w-full sm:max-w-[320px]"
            />
            <div className="relative w-full sm:w-[220px]">
              <select
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
                className="pp-input h-12 w-full appearance-none pr-10 text-sm text-[#111827]"
              >
                {FILTER_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              <ChevronDown size={16} className="pointer-events-none absolute right-4 top-4 text-[#98a2b3]" />
            </div>
          </div>
          <div className="text-sm text-[#667085]">
            {isLoading ? 'Loading…' : activeTab === 'batches' ? `${batches.length} visible` : `${filteredInventory.length} visible`}
          </div>
        </div>
      </div>

      <Tabs
        items={INVENTORY_TABS.map((tab) => ({ ...tab, count: tabCounts[tab.value] || 0 }))}
        value={activeTab}
        onChange={(value) => {
          setActiveTab(value);
          setSelectedIds([]);
        }}
      />

      {selectedIds.length && activeTab !== 'batches' ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-[18px] border border-[#e5e7eb] bg-white px-4 py-3">
          <p className="text-sm font-semibold text-[#111827]">{selectedIds.length} selected</p>
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
        </div>
      ) : null}

      {activeTab === 'batches' ? (
        <div className="overflow-hidden rounded-[18px] border border-[#e5e7eb] bg-white">
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="bg-[#f8fafc]">
                <tr>
                  <th className="px-4 py-3 font-semibold text-[#667085]">Batch</th>
                  <th className="px-4 py-3 font-semibold text-[#667085]">Items</th>
                  <th className="px-4 py-3 font-semibold text-[#667085]">Photos</th>
                  <th className="px-4 py-3 font-semibold text-[#667085]">Status</th>
                </tr>
              </thead>
              <tbody>
                {batches.length ? (
                  batches.map((batch) => (
                    <tr key={batch.id} className="border-t border-[#e5e7eb] hover:bg-[#fbfdff]">
                      <td className="px-4 py-3 font-medium text-[#111827]">{batch.name}</td>
                      <td className="px-4 py-3 text-[#111827]">{batch.item_count}</td>
                      <td className="px-4 py-3 text-[#111827]">{batch.photo_count}</td>
                      <td className="px-4 py-3">
                        <Badge tone="info">{String(batch.status).toLowerCase()}</Badge>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={4} className="px-4 py-10 text-center text-sm text-[#667085]">
                      No batches available yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div className="overflow-hidden rounded-[18px] border border-[#e5e7eb] bg-white">
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="bg-[#f8fafc]">
                <tr>
                  <th className="w-12 px-4 py-3">
                    <input
                      type="checkbox"
                      checked={filteredInventory.length > 0 && selectedIds.length === filteredInventory.length}
                      onChange={() =>
                        setSelectedIds(selectedIds.length === filteredInventory.length ? [] : filteredInventory.map((item) => item.id))
                      }
                      className="h-4 w-4 rounded border-[#cbd5e1] text-[#2563eb] focus:ring-[#2563eb]"
                    />
                  </th>
                  <th className="px-4 py-3 font-semibold text-[#667085]">Item</th>
                  <th className="px-4 py-3 font-semibold text-[#667085]">Photo count</th>
                  <th className="px-4 py-3 font-semibold text-[#667085]">Status</th>
                  <th className="px-4 py-3 font-semibold text-[#667085]">Batch</th>
                  <th className="px-4 py-3 font-semibold text-[#667085]">Value</th>
                  <th className="px-4 py-3 font-semibold text-[#667085]">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredInventory.length ? (
                  filteredInventory.map((item) => {
                    const status = getInventoryStatus(item);
                    return (
                      <tr key={item.id} className="border-t border-[#e5e7eb] hover:bg-[#fbfdff]">
                        <td className="px-4 py-3">
                          <input
                            type="checkbox"
                            checked={selectedIds.includes(item.id)}
                            onChange={() => toggleRow(item.id)}
                            className="h-4 w-4 rounded border-[#cbd5e1] text-[#2563eb] focus:ring-[#2563eb]"
                          />
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-3">
                            <div className="h-11 w-11 overflow-hidden rounded-[12px] bg-[#f2f4f7]">
                              {item.image_urls?.[0] ? (
                                <img src={toPublicImageUrl(item.image_urls[0])} alt={item.title || `Item ${item.id}`} className="h-full w-full object-cover" />
                              ) : null}
                            </div>
                            <div className="min-w-[220px]">
                              <p className="truncate font-semibold text-[#111827]">{item.title || `Listing #${item.id}`}</p>
                              <p className="mt-1 text-xs text-[#667085]">#{item.id}</p>
                            </div>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-[#111827]">{item.image_urls?.length || 0}</td>
                        <td className="px-4 py-3">
                          <Badge tone={status.tone}>{status.label}</Badge>
                        </td>
                        <td className="px-4 py-3 text-[#667085]">{getBatchLabel(item, storageBatches)}</td>
                        <td className="px-4 py-3 font-medium text-[#111827]">${Number(item.price || item.suggested_price || 0).toFixed(0)}</td>
                        <td className="px-4 py-3">
                          <Button variant="outline" size="sm" onClick={() => setEditingListing(item)}>
                            <PencilLine size={14} />
                            Edit photo
                          </Button>
                        </td>
                      </tr>
                    );
                  })
                ) : (
                  <tr>
                    <td colSpan={7} className="px-4 py-10 text-center text-sm text-[#667085]">
                      No inventory records in this view.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

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
