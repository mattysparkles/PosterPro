import { useEffect, useMemo, useState } from 'react';
import { Download, Info, RefreshCcw, ShoppingCart, Wallet } from 'lucide-react';
import toast from 'react-hot-toast';

import AppShell from '../components/layout/AppShell';
import Button from '../components/ui/button';
import DataTableCard from '../components/ui/data-table-card';
import EmptyState from '../components/ui/empty-state';
import FormSection from '../components/ui/form-section';
import Input from '../components/ui/input';
import MetricCard from '../components/ui/metric-card';
import PageHeader from '../components/ui/page-header';
import SectionPanel from '../components/ui/section-panel';
import StatusPill from '../components/ui/status-pill';
import HealthIndicator from '../components/ui/health-indicator';
import { useAuth } from '../contexts/AuthContext';
import {
  fetchAutonomousConfig,
  fetchSaleDetectionSettings,
  fetchSalesDashboard,
  reconcileSale,
  toggleAutonomousMode,
  updateSaleDetails,
  updateSaleDetectionSettings,
} from '../lib/api';

const MARKETPLACES = ['ebay', 'etsy', 'poshmark', 'mercari', 'depop', 'whatnot', 'vinted'];

function formatTime(value) {
  if (!value) return 'Pending';
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(value));
}

export default function SalesPage() {
  const { user } = useAuth();
  const [dashboard, setDashboard] = useState({ summary: { by_platform: {} }, sales: [] });
  const [autonomousConfig, setAutonomousConfig] = useState({ autonomous_mode: true, autonomous_dry_run: true });
  const [platformSettings, setPlatformSettings] = useState([]);
  const [activeSale, setActiveSale] = useState(null);
  const [search, setSearch] = useState('');
  const [sortBy, setSortBy] = useState('sold_at');
  const [sortDir, setSortDir] = useState('desc');

  const reload = async () => {
    if (!user?.id) return;
    const [salesData, autoData, settings] = await Promise.all([
      fetchSalesDashboard(null, 200, { search, sortBy, sortDir }),
      fetchAutonomousConfig(),
      fetchSaleDetectionSettings(user.id),
    ]);
    setDashboard(salesData);
    setAutonomousConfig(autoData);
    setPlatformSettings(settings.marketplaces || MARKETPLACES);
  };

  useEffect(() => {
    reload();
    const interval = setInterval(reload, 20000);
    return () => clearInterval(interval);
  }, [user?.id, search, sortBy, sortDir]);

  const summaryCards = useMemo(() => {
    const summary = dashboard.summary || {};
    const unmatched = (dashboard.sales || []).filter((sale) => !sale.matched).length;
    return [
      { label: 'Gross sales', value: `$${Number(summary.gross || 0).toFixed(2)}`, detail: 'Top-line sales captured across tracked channels.' },
      { label: 'Net profit', value: `$${Number(summary.total_profit || 0).toFixed(2)}`, detail: 'Profit after fees and shipping when available.' },
      { label: 'Sales detected', value: summary.total_sales || 0, detail: 'Completed sales rows pulled into PosterPro.' },
      { label: 'Units sold', value: summary.units || 0, detail: 'Total item quantity sold in the visible history.' },
      { label: 'Needs review', value: unmatched, detail: unmatched ? 'Sales not yet linked to a local listing.' : 'All visible sales are linked to local listings.' },
    ];
  }, [dashboard]);

  const platformRows = useMemo(
    () =>
      MARKETPLACES.map((marketplace) => {
        const platformSummary = dashboard.summary?.by_platform?.[marketplace] || {};
        return {
          marketplace,
          enabled: platformSettings.includes(marketplace),
          sales: platformSummary.total_sales || 0,
          gross: platformSummary.gross || 0,
          units: platformSummary.units || 0,
          fees_actual: platformSummary.fees_actual || 0,
          shipping_cost: platformSummary.shipping_cost || 0,
          promotional_fees: platformSummary.promotional_fees || 0,
          marketplace_fees: platformSummary.marketplace_fees || 0,
          profit: platformSummary.profit || 0,
        };
      }),
    [dashboard.summary, platformSettings],
  );

  const salesSubnav = useMemo(
    () => ({
      eyebrow: 'Sales CMS',
      title: 'Orders & Detection',
      description: 'Jump between detection policy, marketplace mix, and the live sales timeline from one sales-specific rail.',
      sections: [
        {
          label: 'Sales Views',
          items: [
            { key: 'policy', label: 'Detection Policy', active: false, description: 'Marketplace polling controls', onClick: () => document.getElementById('sales-policy')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) },
            { key: 'mix', label: 'Marketplace Mix', active: false, badge: platformRows.length, description: 'Sales by channel', onClick: () => document.getElementById('mix')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) },
            { key: 'timeline', label: 'Sales Timeline', active: false, badge: (dashboard.sales || []).length, description: 'Latest orders', onClick: () => document.getElementById('timeline')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) },
          ],
        },
      ],
    }),
    [dashboard.sales, platformRows.length],
  );

  const exportCsv = () => {
    const rows = ['id,platform,amount,currency,quantity,fees_actual,shipping_cost,promotional_fees,marketplace_fees,profit,roi_percentage,sold_at,status,order_id'];
    (dashboard.sales || []).forEach((sale) => {
      rows.push(
        [
          sale.id,
          sale.platform,
          sale.amount || '',
          sale.currency,
          sale.quantity,
          sale.fees_actual || '',
          sale.shipping_cost || '',
          sale.promotional_fees || '',
          sale.marketplace_fees || '',
          sale.profit || '',
          sale.roi_percentage || '',
          sale.sold_at || '',
          sale.status,
          sale.marketplace_order_id || '',
        ]
          .map((cell) => `"${String(cell).replaceAll('"', '""')}"`)
          .join(','),
      );
    });

    const blob = new Blob([rows.join('\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', `posterpro-sales-${new Date().toISOString().slice(0, 10)}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  return (
    <AppShell
      active="/sales"
      title="Sales"
      autonomousConfig={autonomousConfig}
      onToggleAutonomous={async () => {
        await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
        await reload();
      }}
      contentWidth="wide"
      subnav={salesSubnav}
    >
      <PageHeader
        title="Sales and orders"
        description="Monitor marketplace sale detection, export bookkeeping data, and finalize per-order cost details without leaving the command center."
        actions={
          <>
            <Button variant="outline" onClick={reload}>
              <RefreshCcw size={16} />
              Refresh
            </Button>
            <Button onClick={exportCsv}>
              <Download size={16} />
              Export CSV
            </Button>
          </>
        }
      />

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {summaryCards.map((card) => (
          <MetricCard key={card.label} label={card.label} value={card.value} detail={card.detail} />
        ))}
      </section>

      <section id="sales-policy" className="grid gap-5 xl:grid-cols-[minmax(0,1.05fr)_minmax(340px,0.95fr)]">
        <SectionPanel title="Detection policy" description="Choose which channels participate in sale polling and auto-delist workflow.">
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {MARKETPLACES.map((marketplace) => {
              const enabled = platformSettings.includes(marketplace);
              return (
                <button
                  key={marketplace}
                  type="button"
                  className={`rounded-[14px] border p-4 text-left transition ${
                    enabled ? 'border-[#bfd2ff] bg-[#f8fbff]' : 'border-[#e5e7eb] bg-white hover:bg-[#f9fafb]'
                  }`}
                  onClick={async () => {
                    const next = enabled ? platformSettings.filter((name) => name !== marketplace) : [...platformSettings, marketplace];
                    setPlatformSettings(next);
                    await updateSaleDetectionSettings(user.id, next);
                  }}
                >
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-semibold capitalize text-[#101828]">{marketplace}</p>
                    <StatusPill status={enabled ? 'success' : 'default'} label={enabled ? 'Enabled' : 'Off'} />
                  </div>
                  <p className="mt-2 text-sm text-[#667085]">Sale polling and cross-channel delist logic {enabled ? 'will' : 'will not'} include this marketplace.</p>
                </button>
              );
            })}
          </div>
        </SectionPanel>

        <SectionPanel title="Workflow notes" description="Use this sales surface for order hygiene, not just reporting.">
          <div className="space-y-3">
            <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
              <HealthIndicator healthy={platformSettings.length > 0} label={platformSettings.length ? `${platformSettings.length} detectors enabled` : 'No detectors enabled'} />
            </div>
            {[
              'PosterPro refreshes the sales timeline every 20 seconds so order events do not drift too far from reality.',
              'Editing fees and shipping here keeps the analytics layer closer to real margin instead of gross-only reporting.',
              'Detection settings should match the real channels where items can sell, otherwise auto-delist trust drops.',
            ].map((item) => (
              <div key={item} className="rounded-[12px] border border-[#e5e7eb] bg-white p-4 text-sm text-[#667085]">
                {item}
              </div>
            ))}
          </div>
        </SectionPanel>
      </section>

      <div id="mix">
      <DataTableCard
        title="Marketplace sales mix"
        description="Current sales, gross totals, fees, and profit by channel included in the sale detector."
        columns={[
            { key: 'marketplace', label: 'Marketplace', render: (row) => <span className="capitalize">{row.marketplace}</span> },
            { key: 'enabled', label: 'Polling', render: (row) => <StatusPill status={row.enabled ? 'success' : 'default'} label={row.enabled ? 'Enabled' : 'Disabled'} /> },
            { key: 'sales', label: 'Sales' },
          { key: 'units', label: 'Units' },
          { key: 'gross', label: 'Gross', render: (row) => `$${Number(row.gross || 0).toFixed(2)}` },
          { key: 'fees_actual', label: 'Fees', render: (row) => `$${Number(row.fees_actual || 0).toFixed(2)}` },
          { key: 'shipping_cost', label: 'Shipping', render: (row) => `$${Number(row.shipping_cost || 0).toFixed(2)}` },
          { key: 'promotional_fees', label: 'Promo', render: (row) => `$${Number(row.promotional_fees || 0).toFixed(2)}` },
          { key: 'profit', label: 'Profit', render: (row) => `$${Number(row.profit || 0).toFixed(2)}` },
        ]}
        rows={platformRows}
        rowKey={(row) => row.marketplace}
        emptyState={<EmptyState title="No marketplace sales yet" description="Sales mix will appear here after marketplaces start reporting completed orders." className="border-0 p-0 py-6" />}
      />
      </div>

      <div id="timeline">
      <DataTableCard
        title="Sales timeline"
        description="Latest detected sales across connected marketplaces. Select a row to finish bookkeeping details."
        toolbar={
          <div className="flex flex-wrap items-center gap-2">
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search listing, platform, order..."
              className="w-full md:w-72"
            />
            <select
              value={sortBy}
              onChange={(event) => setSortBy(event.target.value)}
              className="h-10 rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828]"
            >
              <option value="sold_at">Sold at</option>
              <option value="amount">Amount</option>
              <option value="profit">Profit</option>
              <option value="fees_actual">Fees</option>
              <option value="shipping_cost">Shipping</option>
              <option value="platform">Marketplace</option>
            </select>
            <select
              value={sortDir}
              onChange={(event) => setSortDir(event.target.value)}
              className="h-10 rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828]"
            >
              <option value="desc">Descending</option>
              <option value="asc">Ascending</option>
            </select>
          </div>
        }
        columns={[
          {
            key: 'listing',
            label: 'Listing',
            cellClassName: 'min-w-[260px]',
            render: (sale) => (
              <div>
                <p className="font-medium text-[#101828]">{sale.listing_title || 'Unmatched sale'}</p>
                <p className="mt-1 text-xs text-[#667085]">
                  {sale.listing_id ? `Listing #${sale.listing_id}` : `Marketplace listing ${sale.marketplace_listing_id || 'unknown'}`}
                </p>
              </div>
            ),
          },
          {
            key: 'platform',
            label: 'Marketplace',
            render: (sale) => (
              <div>
                <p className="font-medium capitalize text-[#101828]">{sale.platform}</p>
                <p className="mt-1 text-xs text-[#667085]">Order {sale.marketplace_order_id || 'unknown'}</p>
              </div>
            ),
          },
          { key: 'amount', label: 'Amount', render: (sale) => `$${Number(sale.amount || 0).toFixed(2)}` },
          { key: 'quantity', label: 'Qty', render: (sale) => sale.quantity || 1 },
          { key: 'fees_actual', label: 'Fees', render: (sale) => `$${Number(sale.fees_actual || 0).toFixed(2)}` },
          { key: 'shipping_cost', label: 'Shipping', render: (sale) => `$${Number(sale.shipping_cost || 0).toFixed(2)}` },
          { key: 'promotional_fees', label: 'Promo', render: (sale) => `$${Number(sale.promotional_fees || 0).toFixed(2)}` },
          { key: 'marketplace_fees', label: 'Marketplace', render: (sale) => `$${Number(sale.marketplace_fees || 0).toFixed(2)}` },
          { key: 'profit', label: 'Profit', render: (sale) => `$${Number(sale.profit || 0).toFixed(2)}` },
          { key: 'sold_at', label: 'Sold at', render: (sale) => formatTime(sale.sold_at) },
          { key: 'status', label: 'Status', render: (sale) => <StatusPill status={sale.status || 'completed'} label={sale.status || 'completed'} /> },
          {
            key: 'note',
            label: 'Automation note',
            cellClassName: 'min-w-[280px]',
            render: (sale) => (
              <div className="flex items-center gap-2 text-sm text-[#667085]">
                <Info size={14} />
                {sale.matched
                  ? 'Matched sales are removed from active inventory and delisted from secondary channels.'
                  : 'Unmatched sale. Reconcile it so PosterPro can mark the local item sold and fan out delists.'}
              </div>
            ),
          },
          {
            key: 'actions',
            label: 'Actions',
            render: (sale) =>
              sale.matched ? null : (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={async (event) => {
                    event.stopPropagation();
                    try {
                      await reconcileSale(sale.id);
                      await reload();
                      toast.success('Sale reconciled and listing marked sold.');
                    } catch (error) {
                      toast.error(error.message);
                    }
                  }}
                >
                  Reconcile
                </Button>
              ),
          },
        ]}
        rows={dashboard.sales || []}
        rowKey={(row) => row.id}
        onRowClick={(sale) => setActiveSale(sale)}
        emptyState={<EmptyState title="No sales detected yet" description="The timeline will populate once PosterPro ingests marketplace sales." className="border-0 p-0 py-6" />}
      />
      </div>

      {activeSale ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#101828]/45 p-4">
          <div className="w-full max-w-2xl rounded-[24px] border border-[#e5e7eb] bg-white p-5 shadow-2xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-xl font-semibold tracking-[-0.03em] text-[#101828]">Sale details · #{activeSale.id}</h2>
                <p className="mt-1 text-sm text-[#667085]">Finalize costs, fees, and notes for this completed order.</p>
              </div>
              <Button variant="ghost" onClick={() => setActiveSale(null)}>
                Close
              </Button>
            </div>

            <div className="mt-5 grid gap-4 md:grid-cols-3">
              <div className="rounded-[14px] border border-[#e5e7eb] bg-[#f8fafc] p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Marketplace</p>
                <p className="mt-2 text-sm font-semibold capitalize text-[#101828]">{activeSale.platform}</p>
              </div>
              <div className="rounded-[14px] border border-[#e5e7eb] bg-[#f8fafc] p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Sale amount</p>
                <p className="mt-2 text-sm font-semibold text-[#101828]">${Number(activeSale.amount || 0).toFixed(2)}</p>
              </div>
              <div className="rounded-[14px] border border-[#e5e7eb] bg-[#f8fafc] p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Sold at</p>
                <p className="mt-2 text-sm font-semibold text-[#101828]">{formatTime(activeSale.sold_at)}</p>
              </div>
            </div>

            <form
              className="mt-5 space-y-4"
              onSubmit={async (event) => {
                event.preventDefault();
                const form = new FormData(event.currentTarget);
                await updateSaleDetails(activeSale.id, {
                  fees_actual: Number(form.get('fees_actual') || 0),
                  shipping_cost: Number(form.get('shipping_cost') || 0),
                  promotional_fees: Number(form.get('promotional_fees') || 0),
                  marketplace_fees: Number(form.get('marketplace_fees') || 0),
                  notes: String(form.get('notes') || ''),
                });
                setActiveSale(null);
                await reload();
              }}
            >
              <div className="grid gap-4 md:grid-cols-2">
                <FormSection title="Settlement" description="Record true platform fees and shipping cost.">
                  <label className="block text-sm font-medium text-[#101828]">
                    Platform fees
                    <input name="fees_actual" type="number" step="0.01" placeholder="0.00" defaultValue={activeSale.fees_actual ?? ''} className="mt-2 h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828]" />
                  </label>
                  <label className="block text-sm font-medium text-[#101828]">
                    Shipping cost
                    <input name="shipping_cost" type="number" step="0.01" placeholder="0.00" defaultValue={activeSale.shipping_cost ?? ''} className="mt-2 h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828]" />
                  </label>
                  <label className="block text-sm font-medium text-[#101828]">
                    Promotional fees
                    <input name="promotional_fees" type="number" step="0.01" placeholder="0.00" defaultValue={activeSale.promotional_fees ?? ''} className="mt-2 h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828]" />
                  </label>
                  <label className="block text-sm font-medium text-[#101828]">
                    Marketplace fees
                    <input name="marketplace_fees" type="number" step="0.01" placeholder="0.00" defaultValue={activeSale.marketplace_fees ?? ''} className="mt-2 h-10 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-sm text-[#101828]" />
                  </label>
                </FormSection>
                <FormSection title="Notes" description="Keep any order-specific context with the sale.">
                  <label className="block text-sm font-medium text-[#101828]">
                    Internal notes
                    <textarea name="notes" placeholder="Carrier issue, partial refund, combined shipment, etc." className="mt-2 h-40 w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 py-2 text-sm text-[#101828]" />
                  </label>
                </FormSection>
              </div>
              <div className="flex justify-end gap-2">
                <Button type="button" variant="outline" onClick={() => setActiveSale(null)}>
                  Cancel
                </Button>
                <Button type="submit">
                  <Wallet size={16} />
                  Save details
                </Button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </AppShell>
  );
}

SalesPage.requireAuth = true;
