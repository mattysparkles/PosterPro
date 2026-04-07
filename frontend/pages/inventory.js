import { useCallback, useEffect, useMemo, useState } from 'react';
import { useReactTable, getCoreRowModel, getFilteredRowModel, flexRender, createColumnHelper } from '@tanstack/react-table';
import { AlertTriangle, Grid3X3, List, Pencil, Plus, RefreshCcw, Tag, Trash2, Undo2 } from 'lucide-react';
import toast from 'react-hot-toast';

import AppShell from '../components/layout/AppShell';
import Badge from '../components/ui/badge';
import Button from '../components/ui/button';
import { Card, CardDescription, CardTitle } from '../components/ui/card';
import Input from '../components/ui/input';
import useDashboardData from '../hooks/useDashboardData';
import { bulkEditInventory, fetchInventory, toggleAutonomousMode } from '../lib/api';

const columnHelper = createColumnHelper();
const TABS = ['All', 'Multi-Quantity', 'Stale'];

function LabelManagerModal({ open, onClose, labels, onSave }) {
  const [value, setValue] = useState('');
  const [color, setColor] = useState('sky');
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4">
      <Card className="w-full max-w-lg space-y-4">
        <CardTitle>Custom Label Manager</CardTitle>
        <CardDescription>Create labels, assign color meaning, and apply them in bulk.</CardDescription>
        <div className="flex gap-2">
          <Input value={value} onChange={(e) => setValue(e.target.value)} placeholder="Label name" />
          <select className="rounded-2xl border border-border bg-background px-3" value={color} onChange={(e) => setColor(e.target.value)}>
            <option value="sky">Sky</option>
            <option value="emerald">Green</option>
            <option value="rose">Rose</option>
            <option value="amber">Amber</option>
          </select>
          <Button
            onClick={() => {
              const next = value.trim();
              if (!next) return;
              onSave([...labels, { name: next, color }]);
              setValue('');
            }}
            title="Create label"
          >
            <Plus size={16} />
            Add
          </Button>
        </div>
        <div className="flex flex-wrap gap-2">
          {labels.map((label) => (
            <Badge key={`${label.name}-${label.color}`} tone="info" className="capitalize">{label.name} • {label.color}</Badge>
          ))}
        </div>
        <div className="flex justify-end">
          <Button variant="outline" onClick={onClose}>Close</Button>
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
  const [selection, setSelection] = useState({});
  const [view, setView] = useState('table');
  const [managedLabels, setManagedLabels] = useState([{ name: 'priority', color: 'rose' }]);
  const [isLabelModalOpen, setIsLabelModalOpen] = useState(false);

  const loadInventory = useCallback(async () => {
    const filters = {
      quantityGtOne: tab === 'Multi-Quantity',
      stale: tab === 'Stale',
    };
    const response = await fetchInventory(filters);
    setInventory(response);
  }, [tab]);

  useEffect(() => {
    loadInventory();
  }, [loadInventory]);

  const filtered = useMemo(() => {
    const q = search.toLowerCase().trim();
    if (!q) return inventory;
    return inventory.filter((item) => {
      const labels = (item.custom_labels || []).join(' ').toLowerCase();
      return `${item.id} ${item.title || ''} ${labels}`.toLowerCase().includes(q);
    });
  }, [inventory, search]);

  const columns = useMemo(
    () => [
      columnHelper.display({
        id: 'select',
        header: () => <span title="Select all shown rows">Select</span>,
        cell: ({ row }) => (
          <input
            type="checkbox"
            checked={!!selection[row.original.id]}
            onChange={(e) => setSelection((prev) => ({ ...prev, [row.original.id]: e.target.checked }))}
          />
        ),
      }),
      columnHelper.accessor('title', { header: 'Listing', cell: (info) => info.getValue() || `Listing #${info.row.original.id}` }),
      columnHelper.accessor('quantity', { header: 'Qty' }),
      columnHelper.accessor('platform_quantities', {
        header: 'Per Platform',
        cell: (info) => {
          const map = info.getValue() || {};
          return (
            <div className="flex flex-wrap gap-1">
              {Object.entries(map).map(([key, value]) => (
                <Badge key={key} tone={Number(value) === info.row.original.quantity ? 'success' : 'danger'}>
                  {key}: {value}
                </Badge>
              ))}
            </div>
          );
        },
      }),
      columnHelper.accessor('custom_labels', {
        header: 'Labels',
        cell: (info) => (
          <div className="flex flex-wrap gap-1">
            {(info.getValue() || []).map((label) => (
              <Badge key={label} tone="info">{label}</Badge>
            ))}
          </div>
        ),
      }),
      columnHelper.accessor('last_refreshed', {
        header: 'Sync status',
        cell: (info) => {
          const stale = !info.getValue() || Date.now() - new Date(info.getValue()).getTime() > 1000 * 60 * 60 * 24 * 7;
          return <Badge tone={stale ? 'danger' : 'success'}>{stale ? 'Stale' : 'Synced'}</Badge>;
        },
      }),
    ],
    [selection]
  );

  const table = useReactTable({
    data: filtered,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

  const selectedIds = Object.entries(selection)
    .filter(([, checked]) => checked)
    .map(([id]) => Number(id));

  const runBulkAction = async (payload, successLabel) => {
    if (!selectedIds.length) {
      toast.error('Select at least one listing first.');
      return;
    }
    await bulkEditInventory({ listing_ids: selectedIds, ...payload });
    toast.success(successLabel);
    await loadInventory();
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
      <Card className="space-y-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <CardTitle>Inventory Command Center</CardTitle>
            <CardDescription>Track quantities, protect against overselling, and keep platform inventory in sync.</CardDescription>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => setView((prev) => (prev === 'table' ? 'grid' : 'table'))} title="Switch table and grid layouts.">
              {view === 'table' ? <Grid3X3 size={16} /> : <List size={16} />}
            </Button>
            <Button variant="outline" onClick={loadInventory} title="Refresh from backend">
              <RefreshCcw size={16} />
            </Button>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {TABS.map((tabName) => (
            <Button key={tabName} variant={tab === tabName ? 'default' : 'secondary'} onClick={() => setTab(tabName)} title={`Filter ${tabName}`}>
              {tabName}
            </Button>
          ))}
          <Input placeholder="Search by title, ID, or label" value={search} onChange={(e) => setSearch(e.target.value)} className="max-w-sm" />
        </div>

        {!!selectedIds.length && (
          <Card className="rounded-2xl border-dashed">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="info">{selectedIds.length} selected</Badge>
              <Button size="sm" onClick={() => runBulkAction({ quantity: 1 }, 'Quantity updated')} title="Edit selected quantities">
                <Pencil size={14} /> Edit
              </Button>
              <Button size="sm" variant="outline" onClick={() => runBulkAction({ delist: true }, 'Delisted from channels')} title="Set all platform quantities to 0">
                <Trash2 size={14} /> Delist
              </Button>
              <Button size="sm" variant="outline" onClick={() => runBulkAction({ relist: true }, 'Relisted on channels')} title="Reset channels to listing quantity">
                <Undo2 size={14} /> Relist
              </Button>
              <Button size="sm" variant="outline" onClick={() => runBulkAction({ add_labels: ['priority'] }, 'Label added')} title="Bulk add labels to selected listings">
                <Tag size={14} /> Add Label
              </Button>
            </div>
          </Card>
        )}

        {view === 'table' ? (
          <div className="overflow-x-auto rounded-2xl border border-border/70">
            <table className="w-full text-left text-sm">
              <thead className="bg-muted/40">
                {table.getHeaderGroups().map((headerGroup) => (
                  <tr key={headerGroup.id}>
                    {headerGroup.headers.map((header) => (
                      <th key={header.id} className="px-3 py-2 font-semibold text-muted-foreground">
                        {flexRender(header.column.columnDef.header, header.getContext())}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map((row) => (
                  <tr key={row.id} className="border-t border-border/70">
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id} className="px-3 py-3 align-top">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {filtered.map((item) => {
              const stale = !item.last_refreshed || Date.now() - new Date(item.last_refreshed).getTime() > 1000 * 60 * 60 * 24 * 7;
              return (
                <Card key={item.id} className="space-y-2 rounded-2xl">
                  <p className="text-sm font-semibold">{item.title || `Listing #${item.id}`}</p>
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Badge tone={item.quantity > 1 ? 'info' : 'default'}>Qty: {item.quantity}</Badge>
                    <Badge tone={stale ? 'danger' : 'success'}>{stale ? <AlertTriangle size={12} /> : 'Synced'}</Badge>
                  </div>
                </Card>
              );
            })}
          </div>
        )}

        <Button variant="secondary" onClick={() => setIsLabelModalOpen(true)} title="Create and manage custom labels with color coding.">
          Manage Labels
        </Button>
      </Card>

      <LabelManagerModal open={isLabelModalOpen} onClose={() => setIsLabelModalOpen(false)} labels={managedLabels} onSave={setManagedLabels} />
    </AppShell>
  );
}
