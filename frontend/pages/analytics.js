import { useEffect, useMemo, useState } from 'react';
import { BarChart3, Download, LineChart as LineChartIcon, PieChart as PieChartIcon, TrendingUp } from 'lucide-react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import AppShell from '../components/layout/AppShell';
import Button from '../components/ui/button';
import { Card, CardDescription, CardTitle } from '../components/ui/card';
import {
  downloadInventoryReportCsv,
  downloadSalesReportCsv,
  fetchAnalyticsDashboard,
  fetchAutonomousConfig,
  toggleAutonomousMode,
} from '../lib/api';

const CHART_COLORS = ['#3b82f6', '#14b8a6', '#a855f7', '#f59e0b', '#ef4444', '#22c55e'];

export default function AnalyticsPage({ theme, setTheme }) {
  const [dashboard, setDashboard] = useState({ kpis: {}, top_items: [], revenue_by_marketplace: [], sales_trend: [] });
  const [periodDays, setPeriodDays] = useState(30);
  const [autonomousConfig, setAutonomousConfig] = useState({ autonomous_mode: true, autonomous_dry_run: true });

  const reload = async (days = periodDays) => {
    const [analyticsData, autonomousData] = await Promise.all([
      fetchAnalyticsDashboard(1, days),
      fetchAutonomousConfig(),
    ]);
    setDashboard(analyticsData);
    setAutonomousConfig(autonomousData);
  };

  useEffect(() => {
    reload(periodDays);
  }, [periodDays]);

  const kpis = useMemo(() => {
    const data = dashboard.kpis || {};
    return [
      { label: 'Revenue', value: `$${Number(data.total_revenue || 0).toFixed(2)}`, icon: TrendingUp },
      { label: 'Orders', value: data.total_sales || 0, icon: BarChart3 },
      { label: 'Avg Order Value', value: `$${Number(data.avg_order_value || 0).toFixed(2)}`, icon: LineChartIcon },
      { label: 'Active Listings', value: data.active_listings || 0, icon: PieChartIcon },
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
      <Card className="mb-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle>Premium Analytics</CardTitle>
            <CardDescription>Actionable trends, top sellers, and marketplace performance in one clear view.</CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
            {[14, 30, 60, 90].map((days) => (
              <Button key={days} variant={periodDays === days ? 'default' : 'outline'} onClick={() => setPeriodDays(days)}>
                {days}d
              </Button>
            ))}
            <Button variant="outline" onClick={() => downloadSalesReportCsv(1)}><Download size={14} /> Sales CSV</Button>
            <Button variant="outline" onClick={() => downloadInventoryReportCsv(1)}><Download size={14} /> Inventory CSV</Button>
          </div>
        </div>
      </Card>

      <section className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {kpis.map((item) => (
          <Card key={item.label} className="bg-gradient-to-br from-card to-accent/30">
            <div className="flex items-center justify-between">
              <CardDescription>{item.label}</CardDescription>
              <item.icon size={16} className="text-primary" />
            </div>
            <CardTitle className="mt-3 text-3xl">{item.value}</CardTitle>
          </Card>
        ))}
      </section>

      <section className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardTitle>Revenue Trend</CardTitle>
          <CardDescription className="mb-4">Daily revenue movement for the selected period.</CardDescription>
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={dashboard.sales_trend || []}>
                <defs>
                  <linearGradient id="revenueGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.5} />
                    <stop offset="95%" stopColor="#3b82f6" stopOpacity={0.05} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.25} />
                <XAxis dataKey="date" hide />
                <YAxis />
                <Tooltip formatter={(value) => [`$${Number(value).toFixed(2)}`, 'Revenue']} />
                <Area type="monotone" dataKey="revenue" stroke="#3b82f6" fill="url(#revenueGradient)" strokeWidth={3} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card>
          <CardTitle>Revenue by Marketplace</CardTitle>
          <CardDescription className="mb-4">Which platforms are driving revenue.</CardDescription>
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie data={dashboard.revenue_by_marketplace || []} dataKey="revenue" nameKey="platform" innerRadius={55} outerRadius={90}>
                  {(dashboard.revenue_by_marketplace || []).map((entry, idx) => (
                    <Cell key={entry.platform} fill={CHART_COLORS[idx % CHART_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip formatter={(value) => `$${Number(value).toFixed(2)}`} />
                <Legend />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </section>

      <section className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <CardTitle>Top Sellers</CardTitle>
          <CardDescription className="mb-4">Highest-performing items by revenue.</CardDescription>
          <div className="h-80">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={dashboard.top_items || []} layout="vertical" margin={{ left: 20 }}>
                <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.2} />
                <XAxis type="number" />
                <YAxis type="category" dataKey="title" width={150} tick={{ fontSize: 12 }} />
                <Tooltip formatter={(value) => `$${Number(value).toFixed(2)}`} />
                <Bar dataKey="revenue" fill="#14b8a6" radius={[0, 8, 8, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card>
          <CardTitle>Marketplace Sales Count</CardTitle>
          <CardDescription className="mb-4">Order volume by marketplace.</CardDescription>
          <div className="h-80">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={dashboard.revenue_by_marketplace || []}>
                <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.2} />
                <XAxis dataKey="platform" />
                <YAxis />
                <Tooltip />
                <Bar dataKey="sales_count" fill="#a855f7" radius={[8, 8, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </section>
    </AppShell>
  );
}
