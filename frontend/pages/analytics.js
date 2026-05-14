import { useEffect, useMemo, useState } from 'react';
import { Download } from 'lucide-react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import AppShell from '../components/layout/AppShell';
import Button from '../components/ui/button';
import MetricCard from '../components/ui/metric-card';
import PageHeader from '../components/ui/page-header';
import SectionPanel from '../components/ui/section-panel';
import { Tabs } from '../components/ui/tabs';
import { useAuth } from '../contexts/AuthContext';
import {
  downloadInventoryReportCsv,
  downloadSalesReportCsv,
  fetchAnalyticsDashboard,
  fetchAutonomousConfig,
  toggleAutonomousMode,
} from '../lib/api';

const CHART_COLORS = ['#2563eb', '#14b8a6', '#f59e0b', '#ef4444'];
const ANALYTICS_TABS = [
  { value: 'revenue', label: 'Revenue' },
  { value: 'profit', label: 'Profit' },
  { value: 'categories', label: 'Categories' },
  { value: 'pricing', label: 'Pricing' },
  { value: 'performance', label: 'Performance' },
];

export default function AnalyticsPage() {
  const { user } = useAuth();
  const [dashboard, setDashboard] = useState({ kpis: {}, top_items: [], revenue_by_marketplace: [], sales_trend: [] });
  const [periodDays, setPeriodDays] = useState(30);
  const [activeTab, setActiveTab] = useState('revenue');
  const [autonomousConfig, setAutonomousConfig] = useState({ autonomous_mode: true, autonomous_dry_run: true });

  const reload = async (days = periodDays) => {
    if (!user?.id) return;
    const [analyticsData, autonomousData] = await Promise.all([
      fetchAnalyticsDashboard(user.id, days),
      fetchAutonomousConfig(),
    ]);
    setDashboard(analyticsData);
    setAutonomousConfig(autonomousData);
  };

  useEffect(() => {
    reload(periodDays);
  }, [periodDays, user?.id]);

  const kpis = useMemo(() => {
    const data = dashboard.kpis || {};
    return [
      { label: 'Revenue', value: `$${Number(data.total_revenue || 0).toFixed(2)}`, detail: 'Gross revenue for the selected period.' },
      { label: 'Profit', value: `$${Number(data.total_profit || 0).toFixed(2)}`, detail: 'Profit after recorded costs.' },
      { label: 'Orders', value: data.total_sales || 0, detail: 'Completed sales count.' },
      { label: 'Active listings', value: data.active_listings || 0, detail: 'Currently active marketplace listings.' },
    ];
  }, [dashboard]);
  const insightCards = useMemo(() => {
    const marketplaces = dashboard.revenue_by_marketplace || [];
    const leader = marketplaces.slice().sort((a, b) => Number(b.revenue || 0) - Number(a.revenue || 0))[0];
    return [
      {
        label: 'Top marketplace',
        value: leader?.platform || 'Pending',
        detail: leader ? `$${Number(leader.revenue || 0).toFixed(2)} revenue in the selected window.` : 'No marketplace revenue recorded yet.',
      },
      {
        label: 'Average order value',
        value: `$${Number(dashboard.kpis?.avg_order_value || 0).toFixed(2)}`,
        detail: 'Blended across completed sales for this period.',
      },
      {
        label: 'Sell-through',
        value: `${Number(dashboard.kpis?.sell_through_rate || 0).toFixed(1)}%`,
        detail: 'How often tracked listing inventory converts into sales.',
      },
    ];
  }, [dashboard]);

  return (
    <AppShell
      active="/analytics"
      title="Analytics"
      autonomousConfig={autonomousConfig}
      onToggleAutonomous={async () => {
        await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
        await reload(periodDays);
      }}
    >
      <PageHeader
        title="Analytics"
        description="Revenue, profit, pricing, and marketplace performance."
        actions={
          <>
            {[14, 30, 60, 90].map((days) => (
              <Button key={days} variant={periodDays === days ? 'default' : 'outline'} onClick={() => setPeriodDays(days)}>
                {days}d
              </Button>
            ))}
            <Button variant="outline" onClick={() => downloadSalesReportCsv(user.id)}>
              <Download size={14} />
              Sales CSV
            </Button>
            <Button variant="outline" onClick={() => downloadInventoryReportCsv(user.id)}>
              <Download size={14} />
              Inventory CSV
            </Button>
          </>
        }
      />

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {kpis.map((item) => (
          <MetricCard key={item.label} label={item.label} value={item.value} detail={item.detail} />
        ))}
      </section>

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(320px,0.9fr)]">
        <SectionPanel title="Analytics reading guide" description="Use these sections as operational decision tools, not just charts for their own sake.">
          <div className="grid gap-3 md:grid-cols-4">
            {[
              ['Revenue', 'Watch topline marketplace movement and gross receipts.'],
              ['Profit', 'Check whether actual margin keeps pace with growth.'],
              ['Pricing', 'See if order value and sell-through are moving in the right direction.'],
              ['Performance', 'Compare channels to decide where operator effort should go next.'],
            ].map(([label, detail]) => (
              <div key={label} className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">{label}</p>
                <p className="mt-2 text-sm text-[#667085]">{detail}</p>
              </div>
            ))}
          </div>
        </SectionPanel>
        <SectionPanel title="Fast insights" description="A few concise readings to orient the operator before deeper analysis.">
          <div className="space-y-3">
            {insightCards.map((card) => (
              <div key={card.label} className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">{card.label}</p>
                <p className="mt-2 text-lg font-semibold text-[#101828]">{card.value}</p>
                <p className="mt-1 text-sm text-[#667085]">{card.detail}</p>
              </div>
            ))}
          </div>
        </SectionPanel>
      </section>

      <Tabs items={ANALYTICS_TABS} value={activeTab} onChange={setActiveTab} />

      {activeTab === 'revenue' ? (
        <div className="grid gap-5 xl:grid-cols-[1.2fr_0.8fr]">
          <SectionPanel title="Revenue trend" description="Daily revenue movement for the selected period.">
            <div className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={dashboard.sales_trend || []}>
                  <defs>
                    <linearGradient id="revenueGradient" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#2563eb" stopOpacity={0.32} />
                      <stop offset="95%" stopColor="#2563eb" stopOpacity={0.04} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.18} />
                  <XAxis dataKey="date" hide />
                  <YAxis />
                  <Tooltip formatter={(value) => [`$${Number(value).toFixed(2)}`, 'Revenue']} />
                  <Area type="monotone" dataKey="revenue" stroke="#2563eb" fill="url(#revenueGradient)" strokeWidth={3} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </SectionPanel>
          <SectionPanel title="Revenue by marketplace" description="Which marketplaces are driving current revenue.">
            <div className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={dashboard.revenue_by_marketplace || []} dataKey="revenue" nameKey="platform" innerRadius={55} outerRadius={90}>
                    {(dashboard.revenue_by_marketplace || []).map((entry, idx) => (
                      <Cell key={entry.platform} fill={CHART_COLORS[idx % CHART_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip formatter={(value) => `$${Number(value).toFixed(2)}`} />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </SectionPanel>
        </div>
      ) : null}

      {activeTab === 'profit' ? (
        <SectionPanel title="Top sellers by profit" description="Highest-performing items for the selected period.">
          <div className="h-96">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={dashboard.top_items || []} layout="vertical" margin={{ left: 20 }}>
                <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.18} />
                <XAxis type="number" />
                <YAxis type="category" dataKey="title" width={170} tick={{ fontSize: 12 }} />
                <Tooltip formatter={(value) => `$${Number(value).toFixed(2)}`} />
                <Bar dataKey="revenue" fill="#14b8a6" radius={[0, 8, 8, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </SectionPanel>
      ) : null}

      {activeTab === 'categories' ? (
        <SectionPanel title="Marketplace mix" description="A quick category-style read on where sales are landing.">
          <div className="space-y-3">
            {(dashboard.revenue_by_marketplace || []).map((row) => (
              <div key={row.platform} className="flex items-center justify-between rounded-[10px] border border-[#e5e7eb] bg-[#f9fafb] px-4 py-3">
                <div>
                  <p className="text-sm font-semibold text-[#101828]">{row.platform}</p>
                  <p className="text-sm text-[#667085]">{row.sales_count} sales</p>
                </div>
                <p className="text-sm font-semibold text-[#101828]">${Number(row.revenue || 0).toFixed(2)}</p>
              </div>
            ))}
          </div>
        </SectionPanel>
      ) : null}

      {activeTab === 'pricing' ? (
        <SectionPanel title="Pricing overview" description="Current order value and sell-through signals.">
          <div className="grid gap-4 md:grid-cols-2">
            <MetricCard label="Average order value" value={`$${Number(dashboard.kpis?.avg_order_value || 0).toFixed(2)}`} detail="Average sale value for this period." />
            <MetricCard label="Sell-through rate" value={`${Number(dashboard.kpis?.sell_through_rate || 0).toFixed(1)}%`} detail="How often listed inventory converts." />
          </div>
        </SectionPanel>
      ) : null}

      {activeTab === 'performance' ? (
        <SectionPanel title="Marketplace sales count" description="Order volume by marketplace.">
          <div className="h-96">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={dashboard.revenue_by_marketplace || []}>
                <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.18} />
                <XAxis dataKey="platform" />
                <YAxis />
                <Tooltip />
                <Bar dataKey="sales_count" fill="#2563eb" radius={[8, 8, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </SectionPanel>
      ) : null}
    </AppShell>
  );
}

AnalyticsPage.requireAuth = true;
