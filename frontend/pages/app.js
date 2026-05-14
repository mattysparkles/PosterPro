import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, ArrowRight, Settings2, Store, Upload } from 'lucide-react';

import AppShell from '../components/layout/AppShell';
import Button from '../components/ui/button';
import DataTable from '../components/ui/data-table';
import EmptyState from '../components/ui/empty-state';
import MetricCard from '../components/ui/metric-card';
import PageHeader from '../components/ui/page-header';
import SectionPanel from '../components/ui/section-panel';
import SetupChecklistPanel from '../components/SetupChecklistPanel';
import StatusPill from '../components/ui/status-pill';
import { useAuth } from '../contexts/AuthContext';
import useDashboardData from '../hooks/useDashboardData';
import { fetchAccountSetupSummary, toggleAutonomousMode } from '../lib/api';

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

export default function Dashboard() {
  const { user } = useAuth();
  const { listings, alerts, autonomousConfig, readyCount, reload } = useDashboardData(user?.id);
  const [setupSummary, setSetupSummary] = useState(null);

  const draftCount = useMemo(
    () => listings.filter((listing) => listing.status !== 'ready' && listing.ebay_publish_status !== 'POSTED' && !listing.ebay_listing_id).length,
    [listings],
  );
  const reviewCount = useMemo(
    () => listings.filter((listing) => listing.needs_review || listing.restricted_review_required).length,
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
  const needsAttentionCount = attentionItems.length + alerts.length;
  const recentActivity = useMemo(
    () =>
      listings
        .slice()
        .sort((a, b) => new Date(b.updated_at || b.created_at || 0) - new Date(a.updated_at || a.created_at || 0))
        .slice(0, 6),
    [listings],
  );
  const blockers = useMemo(() => {
    if (!setupSummary) return [];
    const items = [];
    if (!setupSummary.account_profile_complete) {
      items.push('Add an operator or business name.');
    }
    if (!setupSummary.server_readiness?.ebay_oauth_configured) {
      items.push('Server eBay OAuth credentials are still missing.');
    }
    if (!setupSummary.server_readiness?.openai_configured) {
      items.push('OpenAI is not configured for AI listing enrichment.');
    }
    if (!setupSummary.server_readiness?.photoroom_configured) {
      items.push('PhotoRoom is not configured for background removal.');
    }
    if (!setupSummary.marketplace_connections?.some((item) => item.connected)) {
      items.push('No marketplace account is connected yet.');
    }
    return items;
  }, [setupSummary]);

  const topMetrics = [
    { label: 'Drafts', value: draftCount, note: 'Listings in progress.' },
    { label: 'Review queue', value: reviewCount, note: 'Human approval required.' },
    { label: 'Ready', value: readyCount, note: 'Ready to publish.' },
    { label: 'Live', value: liveCount, note: 'Published listings.' },
  ];

  const stageCards = [
    { label: '1. Intake', value: `${listings.length}`, note: 'Photos and raw inventory enter the system.' },
    { label: '2. Group + enrich', value: `${draftCount}`, note: 'AI and clustering build draft listing data.' },
    { label: '3. Review', value: `${reviewCount}`, note: 'Operator approval catches title, price, and photo issues.' },
    { label: '4. Publish', value: `${readyCount}`, note: 'Approved drafts are ready for marketplace queueing.' },
  ];

  const readinessItems = [
    ['OpenAI', setupSummary?.server_readiness?.openai_configured, 'Needed for richer listing generation and AI assistance.'],
    ['PhotoRoom', setupSummary?.server_readiness?.photoroom_configured, 'Needed for background removal and stronger image cleanup.'],
    ['eBay OAuth', setupSummary?.server_readiness?.ebay_oauth_configured, 'Needed for real account connection and direct eBay publishing.'],
    ['Session security', setupSummary?.server_readiness?.session_secret_configured, 'Needed for strong encrypted secret handling.'],
  ];

  useEffect(() => {
    if (!user?.id) return;
    fetchAccountSetupSummary(user.id)
      .then(setSetupSummary)
      .catch(() => setSetupSummary(null));
  }, [user?.id]);

  return (
    <AppShell
      active="/app"
      title="Dashboard"
      autonomousConfig={autonomousConfig}
      onToggleAutonomous={async () => {
        await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
        await reload();
      }}
    >
      <PageHeader
        title="Dashboard"
        description="Today’s resale workflow overview."
        actions={
          <Link href="/intake">
            <Button>Import photos</Button>
          </Link>
        }
      />

      <section className="grid gap-5 xl:grid-cols-[minmax(0,1.45fr)_360px]">
        <SectionPanel title="Today’s workflow" description="A tighter operating view of what is moving, what is blocked, and where to act next.">
          <div className="grid gap-4">
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              {topMetrics.map((card) => (
                <MetricCard key={card.label} label={card.label} value={card.value} detail={card.note} />
              ))}
            </div>

            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              {stageCards.map((stage) => (
                <div key={stage.label} className="rounded-[14px] border border-[#e5e7eb] bg-[#fcfcfd] p-4">
                  <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">{stage.label}</p>
                  <p className="mt-3 text-2xl font-semibold text-[#101828]">{stage.value}</p>
                  <p className="mt-2 text-sm text-[#667085]">{stage.note}</p>
                </div>
              ))}
            </div>

            <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_280px]">
              <div className="rounded-[16px] border border-[#dbe4f0] bg-[linear-gradient(135deg,#f8fbff_0%,#eef4ff_100%)] p-5">
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#2563eb]">Workspace pulse</p>
                <h2 className="mt-3 text-2xl font-semibold tracking-[-0.03em] text-[#101828]">Keep the queue moving without scanning the whole app.</h2>
                <p className="mt-2 max-w-2xl text-sm leading-6 text-[#475467]">
                  Intake feeds draft creation, review catches risk, and publishing closes the loop. This view keeps those stages visible without stretching each section across the full page.
                </p>
              </div>
              <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-1">
                <div className="rounded-[14px] border border-[#e5e7eb] bg-white p-4">
                  <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Needs attention</p>
                  <p className="mt-3 text-2xl font-semibold text-[#101828]">{needsAttentionCount}</p>
                  <p className="mt-2 text-sm text-[#667085]">Failures, alerts, and setup blockers.</p>
                </div>
                <div className="rounded-[14px] border border-[#e5e7eb] bg-white p-4">
                  <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Automation</p>
                  <div className="mt-3">
                    <StatusPill
                      status={autonomousConfig?.autonomous_mode ? 'success' : 'default'}
                      label={autonomousConfig?.autonomous_mode ? 'Automation on' : 'Automation off'}
                    />
                  </div>
                  <p className="mt-2 text-sm text-[#667085]">Controls how aggressively the workspace advances drafts.</p>
                </div>
                <div className="rounded-[14px] border border-[#e5e7eb] bg-white p-4">
                  <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Fresh activity</p>
                  <p className="mt-3 text-2xl font-semibold text-[#101828]">{recentActivity.length}</p>
                  <p className="mt-2 text-sm text-[#667085]">Recently updated listings in this workspace.</p>
                </div>
              </div>
            </div>
          </div>
        </SectionPanel>

        <div className="space-y-5">
          {setupSummary ? <SetupChecklistPanel setupSummary={setupSummary} /> : null}

          <SectionPanel
            title="Current blockers"
            description="Things preventing this account from operating end to end."
            action={
              <Link href="/settings" className="inline-flex items-center gap-1 text-sm font-medium text-[#2563eb]">
                Open setup
                <ArrowRight size={14} />
              </Link>
            }
          >
            <div className="space-y-3">
              {blockers.length ? (
                blockers.map((item) => (
                  <div key={item} className="rounded-[10px] border border-[#e5e7eb] bg-white px-4 py-3">
                    <p className="text-sm text-[#101828]">{item}</p>
                  </div>
                ))
              ) : (
                <div className="rounded-[10px] border border-[#e5e7eb] bg-white px-4 py-8 text-center">
                  <CheckCircle2 size={18} className="mx-auto text-[#067647]" />
                  <p className="mt-3 text-sm font-medium text-[#101828]">Core setup looks healthy</p>
                  <p className="mt-1 text-sm text-[#667085]">This account can stay focused on inventory, listings, and sales workflow.</p>
                </div>
              )}
            </div>
          </SectionPanel>
        </div>
      </section>

      <section className="grid gap-5 xl:grid-cols-[minmax(0,1.35fr)_360px]">
        <div className="space-y-5">
          <SectionPanel title="Operator control center" description="High-signal links for the next practical action in the workflow.">
            <div className="grid gap-3 md:grid-cols-3">
              <Link href="/intake" className="rounded-[12px] border border-[#e5e7eb] bg-white p-4 transition hover:border-[#bfd2ff] hover:bg-[#f8fbff]">
                <p className="text-sm font-semibold text-[#101828]">Import a folder or batch</p>
                <p className="mt-1 text-sm text-[#667085]">Bring in photos, create a zip batch, and push inventory into the intake pipeline.</p>
              </Link>
              <Link href="/listings?tab=review" className="rounded-[12px] border border-[#e5e7eb] bg-white p-4 transition hover:border-[#bfd2ff] hover:bg-[#f8fbff]">
                <p className="text-sm font-semibold text-[#101828]">Approve waiting drafts</p>
                <p className="mt-1 text-sm text-[#667085]">Open the review queue and validate AI-generated titles, pricing, and photos before publish.</p>
              </Link>
              <Link href="/publishing?tab=sync" className="rounded-[12px] border border-[#e5e7eb] bg-white p-4 transition hover:border-[#bfd2ff] hover:bg-[#f8fbff]">
                <p className="text-sm font-semibold text-[#101828]">Watch publish health</p>
                <p className="mt-1 text-sm text-[#667085]">Track queueing, live rows, and marketplace sync issues from one console.</p>
              </Link>
            </div>
          </SectionPanel>

          <SectionPanel
            title="Recent activity"
            description="Latest listing updates across this workspace."
            action={<Link href="/listings" className="text-sm font-medium text-[#2563eb]">View all</Link>}
          >
            <DataTable
              columns={[
                {
                  key: 'title',
                  label: 'Title',
                  render: (listing) => (
                    <div>
                      <p className="truncate font-medium text-[#101828]">{listing.title || `Listing #${listing.id}`}</p>
                      <p className="mt-1 text-xs text-[#667085]">#{listing.id}</p>
                    </div>
                  ),
                },
                {
                  key: 'status',
                  label: 'Status',
                  render: (listing) => <StatusPill status={listing.status || 'draft'} label={listing.status || 'Draft'} />,
                },
                {
                  key: 'updated',
                  label: 'Updated',
                  render: (listing) => formatTime(listing.updated_at || listing.created_at),
                },
              ]}
              rows={recentActivity}
              rowKey={(row) => row.id}
              emptyState={<EmptyState title="No recent activity" description="Recent listing changes will appear here." className="border-0 p-0 py-6" />}
            />
          </SectionPanel>
        </div>

        <div className="space-y-5">
          <SectionPanel title="System readiness" description="The live dependencies that still control how far automation can go.">
            <div className="grid gap-3">
              {readinessItems.map(([label, ok, note]) => (
                <div key={label} className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-semibold text-[#101828]">{label}</p>
                    <StatusPill status={ok ? 'success' : 'warning'} label={ok ? 'Ready' : 'Missing'} />
                  </div>
                  <p className="mt-2 text-sm text-[#667085]">{note}</p>
                </div>
              ))}
            </div>
          </SectionPanel>

          <SectionPanel title="Needs attention" description="Failures, alerts, and listings that need review.">
            <div className="space-y-3">
              {attentionItems.length ? (
                attentionItems.map((listing) => (
                  <div key={listing.id} className="rounded-[10px] border border-[#e5e7eb] bg-white px-4 py-3">
                    <div className="flex items-start gap-3">
                      <AlertTriangle size={16} className="mt-0.5 shrink-0 text-[#b42318]" />
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-[#101828]">{listing.title || `Listing #${listing.id}`}</p>
                        <p className="mt-1 text-sm text-[#667085]">Review publish or sync status.</p>
                      </div>
                    </div>
                  </div>
                ))
              ) : alerts?.length ? (
                alerts.slice(0, 5).map((alert, index) => (
                  <div key={`${alert.title || 'alert'}-${index}`} className="rounded-[10px] border border-[#e5e7eb] bg-white px-4 py-3">
                    <p className="text-sm font-medium text-[#101828]">{alert.title || 'Alert'}</p>
                    <p className="mt-1 text-sm text-[#667085]">{alert.message || 'Check the latest workflow state.'}</p>
                  </div>
                ))
              ) : (
                <div className="rounded-[10px] border border-[#e5e7eb] bg-white px-4 py-8 text-center">
                  <CheckCircle2 size={18} className="mx-auto text-[#067647]" />
                  <p className="mt-3 text-sm font-medium text-[#101828]">No blockers right now</p>
                  <p className="mt-1 text-sm text-[#667085]">Drafts, publishing, and sync are currently clear.</p>
                </div>
              )}
            </div>
          </SectionPanel>
        </div>
      </section>

      <section className="grid gap-5 xl:grid-cols-[minmax(0,1.35fr)_360px]">
        <SectionPanel title="Next actions" description="The shortest path to a usable reseller workspace.">
          <div className="grid gap-3 md:grid-cols-3">
            <Link href="/intake" className="rounded-[12px] border border-[#e5e7eb] bg-white p-4 transition hover:border-[#bfd2ff] hover:bg-[#f8fbff]">
              <div className="inline-flex h-11 w-11 items-center justify-center rounded-2xl bg-[#eef4ff] text-[#2563eb]">
                <Upload size={18} />
              </div>
              <p className="mt-4 text-sm font-semibold text-[#101828]">Import inventory</p>
              <p className="mt-1 text-sm text-[#667085]">Bring in photos or zip batches to create real listing work.</p>
            </Link>
            <Link href="/settings" className="rounded-[12px] border border-[#e5e7eb] bg-white p-4 transition hover:border-[#bfd2ff] hover:bg-[#f8fbff]">
              <div className="inline-flex h-11 w-11 items-center justify-center rounded-2xl bg-[#eef4ff] text-[#2563eb]">
                <Settings2 size={18} />
              </div>
              <p className="mt-4 text-sm font-semibold text-[#101828]">Finish setup</p>
              <p className="mt-1 text-sm text-[#667085]">Connect marketplaces, save keys, and configure automation.</p>
            </Link>
            <Link href="/publishing" className="rounded-[12px] border border-[#e5e7eb] bg-white p-4 transition hover:border-[#bfd2ff] hover:bg-[#f8fbff]">
              <div className="inline-flex h-11 w-11 items-center justify-center rounded-2xl bg-[#eef4ff] text-[#2563eb]">
                <Store size={18} />
              </div>
              <p className="mt-4 text-sm font-semibold text-[#101828]">Review publishing</p>
              <p className="mt-1 text-sm text-[#667085]">Watch queued listings, live rows, and sync health in one place.</p>
            </Link>
          </div>
        </SectionPanel>

        <SectionPanel title="Workspace summary" description="A compact read on how the dashboard is behaving right now.">
          <div className="grid gap-3">
            <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
              <p className="text-sm font-semibold text-[#101828]">Review pressure</p>
              <p className="mt-2 text-sm text-[#667085]">
                {reviewCount > 0
                  ? `${reviewCount} listing${reviewCount === 1 ? '' : 's'} still need operator approval before publishing can move cleanly.`
                  : 'No listings are waiting for manual approval right now.'}
              </p>
            </div>
            <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
              <p className="text-sm font-semibold text-[#101828]">Draft throughput</p>
              <p className="mt-2 text-sm text-[#667085]">
                {draftCount > 0
                  ? `${draftCount} draft${draftCount === 1 ? '' : 's'} are still moving through enrichment or review.`
                  : 'There are no active draft listings in progress at the moment.'}
              </p>
            </div>
            <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
              <p className="text-sm font-semibold text-[#101828]">Marketplace state</p>
              <p className="mt-2 text-sm text-[#667085]">
                {liveCount > 0
                  ? `${liveCount} listing${liveCount === 1 ? '' : 's'} are already live on marketplace channels.`
                  : 'No listings are live yet, so publishing remains the next meaningful downstream milestone.'}
              </p>
            </div>
          </div>
        </SectionPanel>
      </section>
    </AppShell>
  );
}

Dashboard.requireAuth = true;
