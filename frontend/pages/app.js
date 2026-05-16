import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock3,
  Database,
  Package,
  PlusCircle,
  RefreshCcw,
  Settings2,
  ShoppingCart,
  Store,
  Upload,
} from 'lucide-react';
import { useRouter } from 'next/router';

import AppShell from '../components/layout/AppShell';
import Button from '../components/ui/button';
import DataTableCard from '../components/ui/data-table-card';
import EmptyState from '../components/ui/empty-state';
import MetricCard from '../components/ui/metric-card';
import PageHeader from '../components/ui/page-header';
import QuickActionCard from '../components/ui/quick-action-card';
import SectionPanel from '../components/ui/section-panel';
import StatusPill from '../components/ui/status-pill';
import SetupChecklistPanel from '../components/SetupChecklistPanel';
import { useAuth } from '../contexts/AuthContext';
import useDashboardData from '../hooks/useDashboardData';
import {
  fetchAccountSetupSummary,
  fetchMarketplaceJobsOverview,
  fetchSalesDashboard,
  toggleAutonomousMode,
} from '../lib/api';

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

function toStatusTone(connected, warning = false) {
  if (connected) return 'success';
  if (warning) return 'warning';
  return 'default';
}

export default function Dashboard() {
  const router = useRouter();
  const { user } = useAuth();
  const { listings, alerts, autonomousConfig, readyCount, reload } = useDashboardData(user?.id);
  const [setupSummary, setSetupSummary] = useState(null);
  const [jobsOverview, setJobsOverview] = useState({ import_jobs: [], crosspost_jobs: [] });
  const [salesDashboard, setSalesDashboard] = useState({ summary: {} });
  const [activeSection, setActiveSection] = useState('overview');

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
  const failedPublishCount = useMemo(
    () => listings.filter((listing) => listing.status === 'error' || listing.ebay_publish_status === 'FAILED').length,
    [listings],
  );
  const recentActivity = useMemo(
    () =>
      listings
        .slice()
        .sort((a, b) => new Date(b.updated_at || b.created_at || 0) - new Date(a.updated_at || a.created_at || 0))
        .slice(0, 7),
    [listings],
  );
  const attentionItems = useMemo(
    () => listings.filter((listing) => listing.status === 'error' || listing.ebay_publish_status === 'FAILED').slice(0, 5),
    [listings],
  );

  useEffect(() => {
    if (!user?.id) return;
    Promise.allSettled([
      fetchAccountSetupSummary(user.id),
      fetchMarketplaceJobsOverview(),
      fetchSalesDashboard(user.id, 25),
    ]).then(([setupResult, jobsResult, salesResult]) => {
      setSetupSummary(setupResult.status === 'fulfilled' ? setupResult.value : null);
      setJobsOverview(jobsResult.status === 'fulfilled' ? jobsResult.value || { import_jobs: [], crosspost_jobs: [] } : { import_jobs: [], crosspost_jobs: [] });
      setSalesDashboard(salesResult.status === 'fulfilled' ? salesResult.value || { summary: {} } : { summary: {} });
    });
  }, [user?.id]);

  const allJobs = useMemo(() => [...(jobsOverview.import_jobs || []), ...(jobsOverview.crosspost_jobs || [])], [jobsOverview]);
  const jobsSummary = useMemo(
    () => ({
      queued: allJobs.filter((job) => ['queued', 'running'].includes(String(job.status).toLowerCase())).length,
      failed: allJobs.filter((job) => String(job.status).toLowerCase() === 'failed').length,
      completed: allJobs.filter((job) => String(job.status).toLowerCase() === 'completed').length,
    }),
    [allJobs],
  );

  const blockers = useMemo(() => {
    if (!setupSummary) return [];
    const items = [];
    if (!setupSummary.account_profile_complete) items.push('Add an operator or business name.');
    if (!setupSummary.server_readiness?.ebay_oauth_configured) items.push('Server eBay OAuth credentials are still missing.');
    if (!setupSummary.server_readiness?.openai_configured) items.push('OpenAI is not configured for AI listing enrichment.');
    if (!setupSummary.server_readiness?.photoroom_configured) items.push('PhotoRoom is not configured for background removal.');
    if (!setupSummary.marketplace_connections?.some((item) => item.connected)) items.push('No marketplace account is connected yet.');
    return items;
  }, [setupSummary]);

  const marketplaceWidgets = (setupSummary?.marketplace_connections || []).slice(0, 6);
  const topMetrics = [
    { label: 'Ready to publish', value: readyCount, detail: 'Listings that can move straight into marketplace publishing.' },
    { label: 'Pending review', value: reviewCount, detail: 'Drafts still waiting for operator approval.' },
    { label: 'Live listings', value: liveCount, detail: 'Listings already posted or actively synced.' },
    { label: 'Draft backlog', value: draftCount, detail: 'Items still moving through enrichment and manual edits.' },
  ];
  const workspaceStats = [
    { label: 'Batch jobs', value: jobsSummary.queued, note: 'Queued or running' },
    { label: 'Failures', value: jobsSummary.failed + failedPublishCount, note: 'Jobs plus publish errors' },
    { label: 'Sales gross', value: `$${Number(salesDashboard.summary?.gross || 0).toFixed(0)}`, note: 'Detected channel revenue' },
    { label: 'Units sold', value: salesDashboard.summary?.units || 0, note: 'Completed units' },
  ];
  const readinessRows = [
    ['OpenAI', setupSummary?.server_readiness?.openai_configured, 'AI enrichment and pricing help'],
    ['PhotoRoom', setupSummary?.server_readiness?.photoroom_configured, 'Image cleanup workflows'],
    ['eBay OAuth', setupSummary?.server_readiness?.ebay_oauth_configured, 'Direct eBay publishing'],
    ['Session security', setupSummary?.server_readiness?.session_secret_configured, 'Encrypted secret handling'],
  ];
  const activeBridgeConnectSession = setupSummary?.active_bridge_connect_session || null;
  const recentJobRows = allJobs
    .slice()
    .sort((a, b) => new Date(b.updated_at || b.created_at || 0) - new Date(a.updated_at || a.created_at || 0))
    .slice(0, 6)
    .map((job) => ({
      id: `${job.job_type || 'job'}-${job.id}`,
      job_id: job.id,
      job_type: job.job_type || (job.source_marketplace ? 'Import' : 'Cross-post'),
      reference: job.listing_id ? `Listing #${job.listing_id}` : job.source_listing_reference || job.source_marketplace || 'Pending',
      status: job.status || 'queued',
      updated_at: job.updated_at || job.created_at,
    }));

  const dashboardSections = useMemo(
    () => [
      { key: 'overview', label: 'Overview', description: 'Primary metrics and dashboard summary' },
      { key: 'activity', label: 'Activity', description: 'Recent listing movement and alerts' },
      { key: 'jobs', label: 'Jobs', description: 'Import and cross-post queue status' },
      { key: 'operations', label: 'Operations', description: 'Workflow posture and quick actions' },
      { key: 'channels', label: 'Channels', description: 'Marketplace connection state' },
      { key: 'setup', label: 'Setup', description: 'Checklist and current blockers' },
      { key: 'system', label: 'System', description: 'Runtime readiness and integrations' },
    ],
    [],
  );

  useEffect(() => {
    if (!router.isReady) return;
    const requested = typeof router.query.section === 'string' ? router.query.section : '';
    if (requested && dashboardSections.some((section) => section.key === requested)) {
      setActiveSection(requested);
      return;
    }
    setActiveSection('overview');
  }, [dashboardSections, router.isReady, router.query.section]);

  const selectSection = (key) => {
    setActiveSection(key);
    router.replace(
      {
        pathname: '/app',
        query: key === 'overview' ? {} : { section: key },
      },
      undefined,
      { shallow: true }
    );
  };

  const activeSectionMeta = dashboardSections.find((section) => section.key === activeSection) || dashboardSections[0];

  const renderOverview = () => (
    <div className="space-y-5">
      <SectionPanel
        title="Workspace overview"
        description="Primary throughput, backlog, and publishing posture in one compact operator module."
        action={
          <Link href="/publishing" className="inline-flex items-center gap-1 text-sm font-medium text-[#2563eb]">
            Open publish queue
            <ArrowRight size={14} />
          </Link>
        }
      >
        <div className="space-y-4">
          <div className="pp-dashboard-hero p-5">
            <p className="pp-dashboard-hero__eyebrow">Control room</p>
            <h2 className="pp-dashboard-hero__title mt-3">Run reseller operations from a structured admin workspace.</h2>
            <p className="pp-dashboard-hero__body mt-3">
              The dashboard now behaves more like a CMS backend: choose a section from the left menu, then work through a focused panel on the right instead of scanning one long page.
            </p>
          </div>
          <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-4">
            {topMetrics.map((card) => (
              <MetricCard key={card.label} label={card.label} value={card.value} detail={card.detail} />
            ))}
          </div>
        </div>
      </SectionPanel>
    </div>
  );

  const renderActivity = () => (
    <div className="space-y-5">
      <DataTableCard
        title="Recent listing activity"
        description="Latest changes across drafts, review queue, and live marketplace rows."
        action={<Link href="/listings" className="text-sm font-medium text-[#2563eb]">View all listings</Link>}
        columns={[
          {
            key: 'title',
            label: 'Listing',
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

      <SectionPanel title="Attention feed" description="Errors, alerts, and items that need an operator now.">
        <div className="space-y-3">
          {attentionItems.length ? (
            attentionItems.map((listing) => (
              <div key={listing.id} className="rounded-[12px] border border-[#e5e7eb] bg-white px-4 py-3">
                <div className="flex items-start gap-3">
                  <AlertTriangle size={16} className="mt-0.5 shrink-0 text-[#b42318]" />
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-[#101828]">{listing.title || `Listing #${listing.id}`}</p>
                    <p className="mt-1 text-sm text-[#667085]">Review publish or sync status before requeueing marketplace work.</p>
                  </div>
                </div>
              </div>
            ))
          ) : alerts?.length ? (
            alerts.slice(0, 5).map((alert, index) => (
              <div key={`${alert.title || 'alert'}-${index}`} className="rounded-[12px] border border-[#e5e7eb] bg-white px-4 py-3">
                <p className="text-sm font-medium text-[#101828]">{alert.title || 'Alert'}</p>
                <p className="mt-1 text-sm text-[#667085]">{alert.message || 'Check the latest workflow state.'}</p>
              </div>
            ))
          ) : (
            <div className="rounded-[12px] border border-[#e5e7eb] bg-white px-4 py-8 text-center">
              <Clock3 size={18} className="mx-auto text-[#2563eb]" />
              <p className="mt-3 text-sm font-medium text-[#101828]">No urgent events right now</p>
              <p className="mt-1 text-sm text-[#667085]">Failures, alerts, and queue issues will surface here.</p>
            </div>
          )}
        </div>
      </SectionPanel>
    </div>
  );

  const renderJobs = () => (
    <div className="space-y-5">
      <DataTableCard
        title="Batch processing status"
        description="Recent import and cross-post jobs without leaving the dashboard."
        action={<Link href="/jobs" className="text-sm font-medium text-[#2563eb]">Open jobs console</Link>}
        columns={[
          { key: 'job_type', label: 'Job type' },
          { key: 'reference', label: 'Reference' },
          { key: 'status', label: 'Status', render: (row) => <StatusPill status={row.status} label={row.status} /> },
          { key: 'updated_at', label: 'Updated', render: (row) => formatTime(row.updated_at) },
        ]}
        rows={recentJobRows}
        rowKey={(row) => row.id}
        emptyState={<EmptyState title="No jobs yet" description="Import and cross-post jobs will appear here once work starts moving through the marketplace execution layer." className="border-0 p-0 py-6" />}
      />
      <div className="grid gap-3 md:grid-cols-3">
        <MetricCard label="Queued or running" value={jobsSummary.queued} detail="Current job execution load." />
        <MetricCard label="Completed" value={jobsSummary.completed} detail="Jobs that finished successfully." />
        <MetricCard label="Failed" value={jobsSummary.failed} detail="Jobs that need review or retry." />
      </div>
    </div>
  );

  const renderOperations = () => (
    <div className="space-y-5">
      <SectionPanel title="Operational posture" description="Short-form status modules instead of one oversized narrative dashboard.">
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="rounded-[14px] border border-[#e5e7eb] bg-white p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Marketplace sync</p>
            <div className="mt-3">
              <StatusPill status={failedPublishCount ? 'warning' : 'success'} label={failedPublishCount ? 'Attention needed' : 'Healthy'} />
            </div>
            <p className="mt-2 text-sm text-[#667085]">Derived from current publish failures and live listing state.</p>
          </div>
          <div className="rounded-[14px] border border-[#e5e7eb] bg-white p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Automation mode</p>
            <div className="mt-3">
              <StatusPill status={autonomousConfig?.autonomous_mode ? 'success' : 'default'} label={autonomousConfig?.autonomous_mode ? 'Automation on' : 'Automation off'} />
            </div>
            <p className="mt-2 text-sm text-[#667085]">Controls how aggressively drafts advance without human intervention.</p>
          </div>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 2xl:grid-cols-4">
          {workspaceStats.map((item) => (
            <div key={item.label} className="rounded-[14px] border border-[#e5e7eb] bg-[#fcfcfd] p-4">
              <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">{item.label}</p>
              <p className="mt-2 text-2xl font-semibold text-[#101828]">{item.value}</p>
              <p className="mt-1 text-sm text-[#667085]">{item.note}</p>
            </div>
          ))}
        </div>
      </SectionPanel>

      <SectionPanel title="Quick actions" description="Most common workflow moves kept in a dedicated command module.">
        <div className="grid gap-3 2xl:grid-cols-2">
          <QuickActionCard href="/intake" icon={Upload} eyebrow="Intake" title="Upload Photos" description="Start a new intake batch with loose photos or a zip import." meta={`${listings.length} items`} />
          <QuickActionCard href="/listings/new" icon={PlusCircle} eyebrow="Listings" title="Create Listing" description="Open the listing workspace directly for a manual item or imported draft." meta={`${draftCount} drafts`} />
          <QuickActionCard href="/publishing" icon={RefreshCcw} eyebrow="Publishing" title="Publish Queue" description="Review approvals, queue health, and live marketplace rows." meta={`${readyCount} ready`} />
          <QuickActionCard href="/sales" icon={ShoppingCart} eyebrow="Revenue" title="Sales & Orders" description="Monitor detected sales and finish bookkeeping adjustments." meta={`${salesDashboard.summary?.units || 0} units`} />
        </div>
      </SectionPanel>
    </div>
  );

  const renderChannels = () => (
    <div className="space-y-5">
      <SectionPanel title="Marketplace connections" description="Channel state visible as a compact admin panel instead of a dashboard sidebar fragment.">
        <div className="space-y-3">
          {activeBridgeConnectSession ? (
            <div className="rounded-[14px] border border-[#fde68a] bg-[#fffbeb] p-4">
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-semibold text-[#101828]">Facebook login waiting</p>
                <StatusPill status="warning" label={String(activeBridgeConnectSession.status || 'waiting_for_login').replace(/_/g, ' ')} />
              </div>
              <p className="mt-2 text-sm text-[#667085]">{activeBridgeConnectSession.message || 'PosterPro is waiting for Facebook authentication in the bridge workspace.'}</p>
              <div className="mt-3">
                <Link href={`/bridge-desktop?connectSessionId=${encodeURIComponent(activeBridgeConnectSession.connect_session_id)}`}>
                  <Button variant="outline" size="sm">Resume Facebook login</Button>
                </Link>
              </div>
            </div>
          ) : null}
          {marketplaceWidgets.length ? (
            <div className="grid gap-3 2xl:grid-cols-2">
              {marketplaceWidgets.map((connection) => (
                <div key={connection.marketplace} className="rounded-[14px] border border-[#e5e7eb] bg-white p-4">
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-semibold text-[#101828]">{connection.display_name || connection.marketplace}</p>
                    <StatusPill
                      status={toStatusTone(connection.connected, connection.workflow_state === 'ready')}
                      label={connection.connected ? 'Connected' : connection.workflow_state === 'ready' ? 'Ready' : 'Setup'}
                    />
                  </div>
                  <p className="mt-2 text-sm text-[#667085]">
                    {connection.account_handle ? `Account: ${connection.account_handle}` : 'No account handle saved yet.'}
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState title="No channel records yet" description="Connect eBay or add manual marketplace records from Settings." className="border-0 p-0 py-8" />
          )}
        </div>
      </SectionPanel>
    </div>
  );

  const renderSetup = () => (
    <div className="space-y-5">
      {setupSummary ? <SetupChecklistPanel setupSummary={setupSummary} /> : null}
      <SectionPanel title="Current blockers" description="Missing setup or workflow conditions that still prevent clean end-to-end operation.">
        <div className="space-y-3">
          {blockers.length ? (
            blockers.map((item) => (
              <div key={item} className="rounded-[12px] border border-[#fecdca] bg-[#fff6f3] px-4 py-3">
                <p className="text-sm text-[#912018]">{item}</p>
              </div>
            ))
          ) : (
            <div className="rounded-[12px] border border-[#d1fadf] bg-[#ecfdf3] px-4 py-8 text-center">
              <CheckCircle2 size={18} className="mx-auto text-[#067647]" />
              <p className="mt-3 text-sm font-medium text-[#101828]">Core setup looks healthy</p>
              <p className="mt-1 text-sm text-[#667085]">This account can stay focused on intake, listings, and publishing throughput.</p>
            </div>
          )}
        </div>
      </SectionPanel>
    </div>
  );

  const renderSystem = () => (
    <div className="space-y-5">
      <SectionPanel title="System readiness" description="Live dependencies that still control automation depth.">
        <div className="grid gap-3 2xl:grid-cols-2">
          {readinessRows.map(([label, ok, note]) => (
            <div key={label} className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <span className="inline-flex h-10 w-10 items-center justify-center rounded-[12px] bg-[#f8fafc] text-[#2563eb]">
                    <Database size={16} />
                  </span>
                  <p className="text-sm font-semibold text-[#101828]">{label}</p>
                </div>
                <StatusPill status={ok ? 'success' : 'warning'} label={ok ? 'Ready' : 'Missing'} />
              </div>
              <p className="mt-2 text-sm text-[#667085]">{note}</p>
            </div>
          ))}
        </div>
      </SectionPanel>
      <div className="grid gap-3 2xl:grid-cols-2">
        <QuickActionCard href="/settings?tab=ebay" icon={Store} eyebrow="Integrations" title="Connect eBay" description="Finish OAuth setup or reconnect the current operator account." meta={setupSummary?.server_readiness?.ebay_oauth_configured ? 'Configured' : 'Needs setup'} />
        <QuickActionCard href="/inventory" icon={Package} eyebrow="Inventory" title="View Inventory" description="Inspect intake, active items, sold units, and storage batches." meta={`${listings.length} tracked`} />
      </div>
    </div>
  );

  const sectionContent = {
    overview: renderOverview(),
    activity: renderActivity(),
    jobs: renderJobs(),
    operations: renderOperations(),
    channels: renderChannels(),
    setup: renderSetup(),
    system: renderSystem(),
  };

  return (
    <AppShell
      active="/app"
      title="Overview"
      autonomousConfig={autonomousConfig}
      onToggleAutonomous={async () => {
        await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
        await reload();
      }}
      contentWidth="wide"
    >
      <PageHeader
        title="Reseller command center"
        description="Use one clean operator dashboard for intake, listing production, marketplace publishing, job flow, and account readiness."
        actions={
          <>
            <Link href="/intake">
              <Button variant="outline">
                <Upload size={16} />
                Upload photos
              </Button>
            </Link>
            <Link href="/listings/new">
              <Button>
                <PlusCircle size={16} />
                Create listing
              </Button>
            </Link>
          </>
        }
      />

      <div className="pp-section-workspace pp-dashboard-theme">
        <aside className="pp-section-workspace__menu space-y-4">
          <div className="pp-dashboard-menu-card p-4">
            <p className="pp-dashboard-menu-label">Dashboard menu</p>
            <p className="pp-dashboard-menu-copy mt-2 text-sm leading-6">Use the left rail like a CMS backend to switch the active workspace panel.</p>
            <div className="mt-4 space-y-1.5">
              {dashboardSections.map((section) => {
                const active = activeSection === section.key;
                return (
                  <button
                    key={section.key}
                    type="button"
                    onClick={() => selectSection(section.key)}
                    className={`pp-dashboard-menu-button px-3 py-3 text-left ${active ? 'is-active' : ''}`}
                  >
                    <p className="pp-dashboard-menu-button__title">{section.label}</p>
                    <p className="pp-dashboard-menu-button__copy">{section.description}</p>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="pp-dashboard-note-card p-4">
            <p className="pp-dashboard-note-label">Current view</p>
            <p className="mt-2 font-[var(--pp-heading-font)] text-base font-semibold tracking-[-0.02em] text-[#172033]">{activeSectionMeta.label}</p>
            <p className="pp-dashboard-note-copy mt-1 text-sm leading-6">{activeSectionMeta.description}</p>
            <div className="mt-4 flex flex-wrap gap-2">
              <StatusPill status={autonomousConfig?.autonomous_mode ? 'success' : 'default'} label={autonomousConfig?.autonomous_mode ? 'Automation on' : 'Automation off'} />
              <StatusPill status={blockers.length ? 'warning' : 'success'} label={blockers.length ? `${blockers.length} blockers` : 'Setup healthy'} />
            </div>
          </div>
        </aside>

        <div className="pp-section-workspace__content space-y-5">
          <div className="pp-dashboard-stage p-5">
            <div className="pp-dashboard-stage__header pb-4">
              <p className="pp-dashboard-stage-label">Workspace panel</p>
              <h2 className="pp-dashboard-stage__title mt-2">{activeSectionMeta.label}</h2>
              <p className="pp-dashboard-stage-copy mt-2 text-sm leading-6">{activeSectionMeta.description}</p>
            </div>
            <div className="pt-5">{sectionContent[activeSection]}</div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

Dashboard.requireAuth = true;
