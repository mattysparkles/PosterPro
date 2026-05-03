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
import SectionCard from '../components/ui/section-card';
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

export default function AnalyticsPage({ theme, setTheme }) {
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

  return (
    <AppShell
      active="/analytics"
      autonomousConfig={autonomousConfig}
      onToggleAutonomous={async () => {
        await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
        await reload(periodDays);
      }}
      theme={theme}
      onToggleTheme={() => {
        const next = theme === 'dark' ? 'light' : 'dark';
        setTheme(next);
        localStorage.setItem('posterpro-theme', next);
        document.documentElement.classList.toggle('dark', next === 'dark');
      }}
    >
      <PageHeader
        eyebrow="Analytics"
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

      <Tabs items={ANALYTICS_TABS} value={activeTab} onChange={setActiveTab} />

      {activeTab === 'revenue' ? (
        <div className="grid gap-5 xl:grid-cols-[1.2fr_0.8fr]">
          <SectionCard title="Revenue trend" description="Daily revenue movement for the selected period.">
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
          </SectionCard>
          <SectionCard title="Revenue by marketplace" description="Which marketplaces are driving current revenue.">
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
          </SectionCard>
        </div>
      ) : null}

      {activeTab === 'profit' ? (
        <SectionCard title="Top sellers by profit" description="Highest-performing items for the selected period.">
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
        </SectionCard>
      ) : null}

      {activeTab === 'categories' ? (
        <SectionCard title="Marketplace mix" description="A quick category-style read on where sales are landing.">
          <div className="space-y-3">
            {(dashboard.revenue_by_marketplace || []).map((row) => (
              <div key={row.platform} className="flex items-center justify-between rounded-[16px] border border-[#e5e7eb] bg-[#f8fafc] px-4 py-3">
                <div>
                  <p className="text-sm font-semibold text-[#111827]">{row.platform}</p>
                  <p className="text-sm text-[#667085]">{row.sales_count} sales</p>
                </div>
                <p className="text-sm font-semibold text-[#111827]">${Number(row.revenue || 0).toFixed(2)}</p>
              </div>
            ))}
          </div>
        </SectionCard>
      ) : null}

      {activeTab === 'pricing' ? (
        <SectionCard title="Pricing overview" description="Current order value and sell-through signals.">
          <div className="grid gap-4 md:grid-cols-2">
            <MetricCard label="Average order value" value={`$${Number(dashboard.kpis?.avg_order_value || 0).toFixed(2)}`} detail="Average sale value for this period." />
            <MetricCard label="Sell-through rate" value={`${Number(dashboard.kpis?.sell_through_rate || 0).toFixed(1)}%`} detail="How often listed inventory converts." />
          </div>
        </SectionCard>
      ) : null}

      {activeTab === 'performance' ? (
        <SectionCard title="Marketplace sales count" description="Order volume by marketplace.">
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
        </SectionCard>
      ) : null}
    </AppShell>
  );
}

AnalyticsPage.requireAuth = true;
