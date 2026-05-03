import Link from 'next/link';
import { useMemo } from 'react';
import { AlertTriangle, ArrowRight, CheckCircle2, Clock3 } from 'lucide-react';

import AppShell from '../components/layout/AppShell';
import Badge from '../components/ui/badge';
import Button from '../components/ui/button';
import { useAuth } from '../contexts/AuthContext';
import useDashboardData from '../hooks/useDashboardData';
import { toggleAutonomousMode } from '../lib/api';

function formatTime(value) {
  if (!value) return 'Pending';
  try {
    return new Intl.DateTimeFormat('en-US', {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    }).format(new Date(value));
  } catch {
    return 'Pending';
  }
}

export default function Dashboard({ theme, setTheme }) {
  const { user } = useAuth();
  const { listings, alerts, analytics, autonomousConfig, readyCount, reload } = useDashboardData(user?.id);

  const draftCount = useMemo(
    () => listings.filter((listing) => listing.status !== 'ready' && listing.ebay_publish_status !== 'POSTED' && !listing.ebay_listing_id).length,
    [listings],
  );
  const liveCount = useMemo(
    () => listings.filter((listing) => listing.ebay_publish_status === 'POSTED' || listing.ebay_listing_id).length,
    [listings],
  );
  const attentionItems = useMemo(
    () => listings.filter((listing) => listing.status === 'error' || listing.ebay_publish_status === 'FAILED').slice(0, 6),
    [listings],
  );
  const recentActivity = useMemo(
    () =>
      listings
        .slice()
        .sort((a, b) => new Date(b.updated_at || b.created_at || 0) - new Date(a.updated_at || a.created_at || 0))
        .slice(0, 6),
    [listings],
  );

  return (
    <AppShell
      active="/app"
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
      <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-sm font-semibold text-[#667085]">Dashboard</p>
          <h1 className="mt-1 text-[2rem] font-semibold tracking-[-0.04em] text-[#111827]">Today&apos;s listing and inventory overview.</h1>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link href="/inventory">
            <Button variant="outline">
              Open inventory
            </Button>
          </Link>
          <Link href="/listings">
            <Button>
              Open listings
              <ArrowRight size={16} />
            </Button>
          </Link>
          <Link href="/inventory">
            <Button variant="outline">Open inventory</Button>
          </Link>
        </div>
      </div>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {[
          { label: 'Draft queue', value: draftCount, note: 'Listings still being prepared.' },
          { label: 'Ready to publish', value: readyCount, note: 'Listings ready to send live.' },
          { label: 'Live listings', value: liveCount, note: 'Published marketplace listings.' },
          { label: 'Sales', value: analytics?.total_sales || 0, note: 'Completed orders in analytics.' },
        ].map((card) => (
          <div key={card.label} className="rounded-[18px] border border-[#e5e7eb] bg-white p-5">
            <p className="text-sm font-semibold text-[#667085]">{card.label}</p>
            <p className="mt-3 text-[2rem] font-semibold tracking-[-0.04em] text-[#111827]">{card.value}</p>
            <p className="mt-2 text-sm text-[#667085]">{card.note}</p>
          </div>
        ))}
      </section>

      <section className="grid gap-5 xl:grid-cols-[minmax(0,1.2fr)_minmax(320px,0.8fr)]">
        <div className="rounded-[18px] border border-[#e5e7eb] bg-white">
          <div className="flex items-center justify-between border-b border-[#e5e7eb] px-5 py-4">
            <div>
              <h2 className="text-base font-semibold text-[#111827]">Recent activity</h2>
              <p className="text-sm text-[#667085]">Latest listing updates across this workspace.</p>
            </div>
            <Link href="/listings" className="text-sm font-semibold text-[#2563eb]">
              View all
            </Link>
          </div>
          <div className="divide-y divide-[#e5e7eb]">
            {recentActivity.length ? (
              recentActivity.map((listing) => (
                <div key={listing.id} className="flex items-center justify-between gap-4 px-5 py-4">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-[#111827]">{listing.title || `Listing #${listing.id}`}</p>
                    <p className="mt-1 text-sm text-[#667085]">Updated {formatTime(listing.updated_at || listing.created_at)}</p>
                  </div>
                  <Badge tone={listing.status === 'ready' ? 'success' : listing.status === 'error' ? 'danger' : 'default'}>
                    {listing.status || 'draft'}
                  </Badge>
                </div>
              ))
            ) : (
              <div className="px-5 py-10 text-sm text-[#667085]">No recent listing activity yet.</div>
            )}
          </div>
        </div>

        <div className="rounded-[18px] border border-[#e5e7eb] bg-white">
          <div className="border-b border-[#e5e7eb] px-5 py-4">
            <h2 className="text-base font-semibold text-[#111827]">Needs attention</h2>
            <p className="text-sm text-[#667085]">Failures, alerts, and listings that need review.</p>
          </div>
          <div className="space-y-3 px-5 py-4">
            {attentionItems.length ? (
              attentionItems.map((listing) => (
                <div key={listing.id} className="rounded-[16px] border border-[#e5e7eb] bg-[#fbfdff] px-4 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-[#111827]">{listing.title || `Listing #${listing.id}`}</p>
                      <p className="mt-1 text-sm text-[#667085]">Publish or sync needs review.</p>
                    </div>
                    <AlertTriangle size={16} className="mt-0.5 shrink-0 text-[#b42318]" />
                  </div>
                </div>
              ))
            ) : alerts?.length ? (
              alerts.slice(0, 5).map((alert, index) => (
                <div key={`${alert.title || 'alert'}-${index}`} className="rounded-[16px] border border-[#e5e7eb] bg-[#fbfdff] px-4 py-3">
                  <div className="flex items-start gap-3">
                    <Clock3 size={16} className="mt-0.5 shrink-0 text-[#2563eb]" />
                    <div>
                      <p className="text-sm font-semibold text-[#111827]">{alert.title || 'Alert'}</p>
                      <p className="mt-1 text-sm text-[#667085]">{alert.message || 'Check the latest workflow state.'}</p>
                    </div>
                  </div>
                </div>
              ))
            ) : (
              <div className="rounded-[16px] border border-[#e5e7eb] bg-[#fbfdff] px-4 py-8 text-center">
                <CheckCircle2 size={18} className="mx-auto text-[#067647]" />
                <p className="mt-3 text-sm font-semibold text-[#111827]">No blockers right now</p>
                <p className="mt-1 text-sm text-[#667085]">Drafts, publishing, and sync are currently clear.</p>
              </div>
            )}
          </div>
        </div>
      </section>
    </AppShell>
  );
}

Dashboard.requireAuth = true;
