import { useEffect, useMemo, useState } from 'react';
import { Download, Info } from 'lucide-react';

import AppShell from '../components/layout/AppShell';
import Button from '../components/ui/button';
import { Card, CardDescription, CardTitle } from '../components/ui/card';
import {
  fetchAutonomousConfig,
  fetchSaleDetectionSettings,
  fetchSalesDashboard,
  toggleAutonomousMode,
  updateSaleDetails,
  updateSaleDetectionSettings,
} from '../lib/api';

const MARKETPLACES = ['ebay', 'poshmark', 'mercari', 'depop', 'whatnot', 'vinted'];

export default function SalesPage({ theme, setTheme }) {
  const [dashboard, setDashboard] = useState({ summary: { by_platform: {} }, sales: [] });
  const [autonomousConfig, setAutonomousConfig] = useState({ autonomous_mode: true, autonomous_dry_run: true });
  const [platformSettings, setPlatformSettings] = useState([]);
  const [activeSale, setActiveSale] = useState(null);

  const reload = async () => {
    const [salesData, autoData, settings] = await Promise.all([
      fetchSalesDashboard(1, 200),
      fetchAutonomousConfig(),
      fetchSaleDetectionSettings(1),
    ]);
    setDashboard(salesData);
    setAutonomousConfig(autoData);
    setPlatformSettings(settings.marketplaces || MARKETPLACES);
  };

  useEffect(() => {
    reload();
    const interval = setInterval(reload, 20000);
    return () => clearInterval(interval);
  }, []);

  const summaryCards = useMemo(() => {
    const summary = dashboard.summary || {};
    return [
      { label: 'Gross Sales', value: `$${(summary.gross || 0).toFixed(2)}` },
      { label: 'Sales Detected', value: summary.total_sales || 0 },
      { label: 'Units Sold', value: summary.units || 0 },
    ];
  }, [dashboard]);

  const exportCsv = () => {
    const rows = ['id,platform,amount,currency,quantity,sold_at,status,order_id'];
    (dashboard.sales || []).forEach((sale) => {
      rows.push(
        [sale.id, sale.platform, sale.amount || '', sale.currency, sale.quantity, sale.sold_at || '', sale.status, sale.marketplace_order_id || '']
          .map((cell) => `"${String(cell).replaceAll('"', '""')}"`)
          .join(',')
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
      <div className="grid gap-4 md:grid-cols-3">
        {summaryCards.map((card) => (
          <Card key={card.label}>
            <CardDescription>{card.label}</CardDescription>
            <CardTitle className="mt-2 text-2xl">{card.value}</CardTitle>
          </Card>
        ))}
      </div>

      <Card className="mt-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle>Sale Detection Settings</CardTitle>
            <CardDescription>Choose marketplaces included in sale polling + auto-delist flow.</CardDescription>
          </div>
          <Button variant="outline" onClick={exportCsv} title="Export timeline to CSV for bookkeeping.">
            <Download size={16} /> Export
          </Button>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {MARKETPLACES.map((marketplace) => {
            const enabled = platformSettings.includes(marketplace);
            return (
              <button
                key={marketplace}
                type="button"
                title="This sale auto-removed the item from other platforms to prevent double-selling."
                className={`rounded-full border px-3 py-1 text-xs font-semibold ${enabled ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground'}`}
                onClick={async () => {
                  const next = enabled
                    ? platformSettings.filter((name) => name !== marketplace)
                    : [...platformSettings, marketplace];
                  setPlatformSettings(next);
                  await updateSaleDetectionSettings(1, next);
                }}
              >
                {marketplace}
              </button>
            );
          })}
        </div>
      </Card>

      <Card className="mt-4">
        <CardTitle>Sales Timeline</CardTitle>
        <CardDescription className="mb-4">Live feed refreshes every 20 seconds.</CardDescription>
        <div className="space-y-3">
          {(dashboard.sales || []).map((sale) => (
            <button
              key={sale.id}
              type="button"
              className="w-full rounded-2xl border border-border/70 p-3 text-left transition hover:bg-muted/40"
              onClick={() => setActiveSale(sale)}
            >
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-semibold uppercase tracking-wide text-primary">{sale.platform}</p>
                  <p className="text-sm text-muted-foreground">Order {sale.marketplace_order_id || 'unknown'}</p>
                </div>
                <p className="text-lg font-bold">${Number(sale.amount || 0).toFixed(2)}</p>
              </div>
              <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
                <Info size={14} />
                This sale auto-removed the item from other platforms to prevent double-selling.
              </div>
            </button>
          ))}
          {!dashboard.sales?.length && <p className="text-sm text-muted-foreground">No sales detected yet.</p>}
        </div>
      </Card>

      {activeSale && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-lg rounded-3xl border border-border bg-card p-5">
            <CardTitle>Complete Details · Sale #{activeSale.id}</CardTitle>
            <CardDescription className="mt-1">Finalize bookkeeping values for this transaction.</CardDescription>
            <form
              className="mt-4 space-y-3"
              onSubmit={async (event) => {
                event.preventDefault();
                const form = new FormData(event.currentTarget);
                await updateSaleDetails(activeSale.id, {
                  fees_actual: Number(form.get('fees_actual') || 0),
                  shipping_cost: Number(form.get('shipping_cost') || 0),
                  notes: String(form.get('notes') || ''),
                });
                setActiveSale(null);
                await reload();
              }}
            >
              <input name="fees_actual" type="number" step="0.01" placeholder="Fees" className="w-full rounded-2xl border border-border bg-background px-3 py-2 text-sm" />
              <input name="shipping_cost" type="number" step="0.01" placeholder="Shipping" className="w-full rounded-2xl border border-border bg-background px-3 py-2 text-sm" />
              <textarea name="notes" placeholder="Notes" className="h-24 w-full rounded-2xl border border-border bg-background px-3 py-2 text-sm" />
              <div className="flex justify-end gap-2">
                <Button type="button" variant="ghost" onClick={() => setActiveSale(null)}>Cancel</Button>
                <Button type="submit">Save details</Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </AppShell>
  );
}
