import { useCallback, useEffect, useMemo, useState } from 'react';
import { useReactTable, getCoreRowModel, flexRender, createColumnHelper } from '@tanstack/react-table';
import { CheckSquare, Eraser, Filter, Grid3X3, List, Pencil, RefreshCcw, Tag, Trash2, Undo2, Zap } from 'lucide-react';
import toast from 'react-hot-toast';

import AppShell from '../components/layout/AppShell';
import Badge from '../components/ui/badge';
import Button from '../components/ui/button';
import { Card, CardDescription, CardTitle } from '../components/ui/card';
import Input from '../components/ui/input';
import useDashboardData from '../hooks/useDashboardData';
import { fetchBulkJob, fetchInventory, runInventoryBulkJob, toggleAutonomousMode } from '../lib/api';

const columnHelper = createColumnHelper();
const TABS = ['All', 'Multi-Quantity', 'Stale'];

function ConfirmBulkModal({ open, onClose, onConfirm, summary }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <Card className="w-full max-w-xl space-y-4 p-2">
        <CardTitle className="text-2xl">Confirm Large Bulk Action</CardTitle>
        <CardDescription>
          This will affect <span className="font-bold text-foreground">{summary.count.toLocaleString()} items</span> and is estimated to take about{' '}
          <span className="font-bold text-foreground">{summary.estimate} minute(s)</span>.
        </CardDescription>
        <p className="rounded-xl bg-muted/60 p-3 text-sm">Action: {summary.label}</p>
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="lg" onClick={onClose}>Cancel</Button>
          <Button size="lg" onClick={onConfirm}>Start bulk job</Button>
        </div>
      </Card>
    </div>
  );
}

function ProgressModal({ job, onClose }) {
  if (!job) return null;
  const pct = job.total_items ? Math.min(100, Math.round((job.processed_items / job.total_items) * 100)) : 0;
  const done = ['completed', 'completed_with_errors', 'failed'].includes(job.status);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <Card className="w-full max-w-xl space-y-4 p-2">
        <CardTitle className="text-xl">Bulk Processing</CardTitle>
        <CardDescription>{job.processed_items} of {job.total_items} completed • status: {job.status}</CardDescription>
        <div className="h-3 w-full rounded-full bg-muted">
          <div className="h-3 rounded-full bg-primary transition-all" style={{ width: `${pct}%` }} />
        </div>
        {!!job.errors?.length && (
          <div className="max-h-40 overflow-auto rounded-xl border border-border p-2 text-xs">
            {job.errors.map((error, idx) => <p key={`${error.listing_id}-${idx}`}>#{error.listing_id}: {error.error}</p>)}
          </div>
        )}
        <div className="flex justify-end gap-2">
          {!done && <Button variant="outline" size="lg" title="Cancellation can be implemented by revoking Celery tasks." disabled>Cancel</Button>}
          <Button size="lg" onClick={onClose}>{done ? 'Close' : 'Hide'}</Button>
        </div>
      </Card>
    </div>
  );
}

export default function InventoryPage({ theme, setTheme }) {
  const { autonomousConfig, reload } = useDashboardData();
  const [tab, setTab] = useState('All');
  const [search, setSearch] = useState('');
  const [inventory, setInventory] = useState([]);
  const [total, setTotal] = useState(0);
  const [selection, setSelection] = useState({});
  const [selectAllMatching, setSelectAllMatching] = useState(false);
  const [view, setView] = useState('table');
  const [page, setPage] = useState(1);
  const [pageSize] = useState(50);
  const [confirmAction, setConfirmAction] = useState(null);
  const [bulkJob, setBulkJob] = useState(null);

  const filters = useMemo(() => ({
    quantityGtOne: tab === 'Multi-Quantity',
    stale: tab === 'Stale',
    search,
    page,
    pageSize,
  }), [page, pageSize, search, tab]);

  const loadInventory = useCallback(async () => {
    const response = await fetchInventory(filters);
    setInventory(response.items || []);
    setTotal(response.total || 0);
  }, [filters]);

  useEffect(() => {
    loadInventory();
  }, [loadInventory]);

  useEffect(() => {
    if (!bulkJob || ['completed', 'completed_with_errors', 'failed'].includes(bulkJob.status)) return;
    const timer = setInterval(async () => {
      const updated = await fetchBulkJob(bulkJob.job_id);
      setBulkJob(updated);
      if (['completed', 'completed_with_errors'].includes(updated.status)) {
        toast.success(`Bulk job finished: ${updated.processed_items}/${updated.total_items}`);
        await loadInventory();
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [bulkJob, loadInventory]);

  const columns = useMemo(() => [
    columnHelper.display({
      id: 'select',
      header: () => <input type="checkbox" checked={inventory.length > 0 && inventory.every((item) => selection[item.id])} onChange={(e) => {
        const checked = e.target.checked;
        setSelection((prev) => {
          const next = { ...prev };
          inventory.forEach((item) => { next[item.id] = checked; });
          return next;
        });
      }} />,
      cell: ({ row }) => <input type="checkbox" checked={!!selection[row.original.id]} onChange={(e) => setSelection((prev) => ({ ...prev, [row.original.id]: e.target.checked }))} />,
    }),
    columnHelper.accessor('title', { header: 'Listing', cell: (info) => info.getValue() || `Listing #${info.row.original.id}` }),
    columnHelper.accessor('quantity', { header: 'Qty' }),
    columnHelper.accessor('custom_labels', { header: 'Labels', cell: (info) => <div className="flex flex-wrap gap-1">{(info.getValue() || []).map((label) => <Badge key={label} tone="info">{label}</Badge>)}</div> }),
    columnHelper.accessor('last_refreshed', { header: 'Status', cell: (info) => {
      const stale = !info.getValue() || Date.now() - new Date(info.getValue()).getTime() > 1000 * 60 * 60 * 24 * 7;
      return <Badge tone={stale ? 'danger' : 'success'}>{stale ? 'Stale' : 'Synced'}</Badge>;
    } }),
  ], [inventory, selection]);

  const table = useReactTable({ data: inventory, columns, getCoreRowModel: getCoreRowModel() });
  const selectedIds = useMemo(() => Object.entries(selection).filter(([, checked]) => checked).map(([id]) => Number(id)), [selection]);
  const effectiveSelectionCount = selectAllMatching ? total : selectedIds.length;

  const queueAction = (action, payload = {}) => {
    if (!selectAllMatching && selectedIds.length === 0) {
      toast.error('Select items or use Select All Matching Filters first.');
      return;
    }
    setConfirmAction({ action, payload, label: action.toUpperCase() });
  };

  const startBulkJob = async () => {
    if (!confirmAction) return;
    const body = {
      action: confirmAction.action,
      payload: confirmAction.payload,
      filters: {
        stale: tab === 'Stale',
        quantity_gt_one: tab === 'Multi-Quantity',
        search: search || null,
      },
      listing_ids: selectAllMatching ? [] : selectedIds,
    };
    const job = await runInventoryBulkJob(body);
    setBulkJob(job);
    setConfirmAction(null);
    toast.success(`Bulk job queued for ${job.total_items} item(s)`);
  };

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
      <Card className="space-y-5">
        <div className="flex items-center justify-between gap-3">
          <div>
            <CardTitle>Inventory Command Center</CardTitle>
            <CardDescription>Limitless bulk operations with explicit progress, chunking, and no silent limits.</CardDescription>
          </div>
          <div className="flex gap-2">
            <Button size="lg" variant="outline" onClick={() => setView((prev) => (prev === 'table' ? 'grid' : 'table'))}>{view === 'table' ? <Grid3X3 size={18} /> : <List size={18} />}</Button>
            <Button size="lg" variant="outline" onClick={loadInventory}><RefreshCcw size={18} /></Button>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {TABS.map((tabName) => <Button key={tabName} size="lg" variant={tab === tabName ? 'default' : 'secondary'} onClick={() => { setPage(1); setTab(tabName); }}><Filter size={16} /> {tabName}</Button>)}
          <Input placeholder="Search title / ID" value={search} onChange={(e) => { setPage(1); setSearch(e.target.value); }} className="max-w-md" />
        </div>

        <Card className="rounded-2xl border-dashed p-4">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Button size="lg" variant={selectAllMatching ? 'default' : 'outline'} title="Select all listings matching current filters across all pages." onClick={() => setSelectAllMatching(true)}><CheckSquare size={18} /> Select All Matching Filters</Button>
            <Button size="lg" variant="outline" onClick={() => { setSelection({}); setSelectAllMatching(false); }}><Eraser size={18} /> Clear Selection</Button>
            <Badge tone="info">This will affect {effectiveSelectionCount.toLocaleString()} items</Badge>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button size="lg" title="Bulk edit selected inventory quantities and platform quantities." onClick={() => queueAction('edit', { quantity: 1 })}><Pencil size={18} /> Bulk Edit</Button>
            <Button size="lg" variant="outline" title="Delist selected listings from all marketplaces." onClick={() => queueAction('delist')}><Trash2 size={18} /> Bulk Delist</Button>
            <Button size="lg" variant="outline" title="Relist selected or filtered listings to restore visibility." onClick={() => queueAction('relist')}><Undo2 size={18} /> Bulk Relist</Button>
            <Button size="lg" variant="outline" title="Add/remove labels in bulk with no quantity limit." onClick={() => queueAction('label', { add_labels: ['priority'], remove_labels: [] })}><Tag size={18} /> Add/Remove Labels</Button>
            <Button size="lg" variant="secondary" title="Relist all stale items to boost visibility — no limit on quantity." onClick={() => queueAction('refresh')}><Zap size={18} /> Refresh Stale</Button>
            <Button size="lg" variant="secondary" title="Schedule marketplace auto-bump in chunked batches for Poshmark, Depop, Grailed, and more." onClick={() => queueAction('autobump', { marketplaces: ['poshmark', 'depop', 'grailed'] })}><RefreshCcw size={18} /> Marketplace Sharer</Button>
          </div>
        </Card>

        {view === 'table' ? (
          <div className="overflow-x-auto rounded-2xl border border-border/70">
            <table className="w-full text-left text-sm">
              <thead className="bg-muted/40">{table.getHeaderGroups().map((headerGroup) => <tr key={headerGroup.id}>{headerGroup.headers.map((header) => <th key={header.id} className="px-3 py-2 font-semibold text-muted-foreground">{flexRender(header.column.columnDef.header, header.getContext())}</th>)}</tr>)}</thead>
              <tbody>{table.getRowModel().rows.map((row) => <tr key={row.id} className="border-t border-border/70">{row.getVisibleCells().map((cell) => <td key={cell.id} className="px-3 py-3 align-top">{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}</tr>)}</tbody>
            </table>
          </div>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{inventory.map((item) => <Card key={item.id} className="space-y-2 rounded-2xl"><p className="text-sm font-semibold">{item.title || `Listing #${item.id}`}</p><Badge tone={item.quantity > 1 ? 'info' : 'default'}>Qty: {item.quantity}</Badge></Card>)}</div>
        )}

        <div className="flex items-center justify-between">
          <p className="text-sm text-muted-foreground">{total.toLocaleString()} total listings</p>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => setPage((prev) => Math.max(1, prev - 1))} disabled={page === 1}>Prev</Button>
            <Badge>{page}</Badge>
            <Button variant="outline" onClick={() => setPage((prev) => prev + 1)} disabled={page * pageSize >= total}>Next</Button>
          </div>
        </div>
      </Card>

      <ConfirmBulkModal
        open={!!confirmAction}
        onClose={() => setConfirmAction(null)}
        onConfirm={startBulkJob}
        summary={{ count: effectiveSelectionCount, estimate: Math.max(1, Math.ceil(effectiveSelectionCount / 150)), label: confirmAction?.label || '' }}
      />
      <ProgressModal job={bulkJob} onClose={() => setBulkJob(null)} />
    </AppShell>
  );
}
