import Link from 'next/link';
import { useEffect, useMemo, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock3,
  Database,
  Package,
  PlusCircle,
  RefreshCcw,
  ShoppingCart,
  Store,
  Upload,
} from 'lucide-react';
import { useRouter } from 'next/router';

import AppShell from '../components/layout/AppShell';
import ActionBar from '../components/ui/action-bar';
import Button from '../components/ui/button';
import DataTableCard from '../components/ui/data-table-card';
import EmptyState from '../components/ui/empty-state';
import MetricCard from '../components/ui/metric-card';
import PageHeader from '../components/ui/page-header';
import QuickActionCard from '../components/ui/quick-action-card';
import SectionPanel from '../components/ui/section-panel';
import StatusPill from '../components/ui/status-pill';
import HealthIndicator from '../components/ui/health-indicator';
import LoadingSkeleton from '../components/ui/loading-skeleton';
import SetupChecklistPanel from '../components/SetupChecklistPanel';
import { useAuth } from '../contexts/AuthContext';
import useDashboardData from '../hooks/useDashboardData';
import {
  fetchAccountSetupSummary,
  fetchMarketplaceJobsOverview,
  runDashboardOperatorCommand,
  fetchSalesDashboard,
  toggleAutonomousMode,
  uploadVineReport,
} from '../lib/api';
import { formatPublishFailureMessage } from '../lib/publish-status';

const DEFAULT_OPERATOR_PROMPT =
  'Lower all item prices by ten percent if they have been posted for more than 1 week on eBay.';

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

function formatMoney(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return 'n/a';
  return `$${amount.toFixed(2)}`;
}

function toStatusTone(connected, warning = false) {
  if (connected) return 'success';
  if (warning) return 'warning';
  return 'default';
}

export default function Dashboard() {
  const router = useRouter();
  const { user } = useAuth();
  const vineFileInputRef = useRef(null);
  const { listings, alerts, autonomousConfig, readyCount, reload } = useDashboardData(user?.id);
  const [setupSummary, setSetupSummary] = useState(null);
  const [jobsOverview, setJobsOverview] = useState({ import_jobs: [], crosspost_jobs: [] });
  const [salesDashboard, setSalesDashboard] = useState({ summary: {} });
  const [activeSection, setActiveSection] = useState('overview');
  const [vineUploading, setVineUploading] = useState(false);
  const [loadingPanels, setLoadingPanels] = useState(false);
  const [operatorPrompt, setOperatorPrompt] = useState(DEFAULT_OPERATOR_PROMPT);
  const [operatorConfirmation, setOperatorConfirmation] = useState('');
  const [operatorCommandResult, setOperatorCommandResult] = useState(null);
  const [operatorCommandRunning, setOperatorCommandRunning] = useState(false);

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
    setLoadingPanels(true);
    Promise.allSettled([
      fetchAccountSetupSummary(user.id),
      fetchMarketplaceJobsOverview(),
      fetchSalesDashboard(user.id, 25),
    ]).then(([setupResult, jobsResult, salesResult]) => {
      setSetupSummary(setupResult.status === 'fulfilled' ? setupResult.value : null);
      setJobsOverview(jobsResult.status === 'fulfilled' ? jobsResult.value || { import_jobs: [], crosspost_jobs: [] } : { import_jobs: [], crosspost_jobs: [] });
      setSalesDashboard(salesResult.status === 'fulfilled' ? salesResult.value || { summary: {} } : { summary: {} });
      setLoadingPanels(false);
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
    { label: 'Ready to publish', value: readyCount, detail: 'Listings that can move straight into marketplace publishing.', href: '/listings?tab=ready' },
    { label: 'Pending review', value: reviewCount, detail: 'Drafts still waiting for operator approval.', href: '/listings?tab=review' },
    { label: 'Live listings', value: liveCount, detail: 'Listings already posted or actively synced.', href: '/listings?tab=published' },
    { label: 'Draft backlog', value: draftCount, detail: 'Items still moving through enrichment and manual edits.', href: '/listings?tab=drafts' },
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
      job_kind: job.source_marketplace ? 'imports' : 'crosspost',
      listing_id: job.listing_id || job.created_listing_id || null,
      job_type: job.job_type || (job.source_marketplace ? 'Import' : 'Cross-post'),
      reference: job.listing_id ? `Listing #${job.listing_id}` : job.source_listing_reference || job.source_marketplace || 'Pending',
      status: job.status || 'queued',
      updated_at: job.updated_at || job.created_at,
    }));

  const dashboardSections = useMemo(
    () => [
      { key: 'overview', label: 'Overview', description: 'Primary metrics and workspace summary' },
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
      { shallow: true },
    );
  };

  const runOperatorCommand = async ({ applyLive = false } = {}) => {
    if (!operatorPrompt.trim()) {
      toast.error('Enter an operator command first.');
      return;
    }
    setOperatorCommandRunning(true);
    try {
      const result = await runDashboardOperatorCommand({
        prompt: operatorPrompt,
        dry_run: !applyLive,
        apply_live: applyLive,
        confirmation_phrase: applyLive ? operatorConfirmation : undefined,
      });
      setOperatorCommandResult(result);
      if (applyLive) {
        toast.success(result?.message || 'Live operator command finished.');
        await reload();
      } else {
        toast.success(result?.message || 'Operator command preview ready.');
      }
    } catch (error) {
      toast.error(error.message || 'Operator command failed.');
    } finally {
      setOperatorCommandRunning(false);
    }
  };

  const renderOverview = () => (
    <div className="space-y-5">
      <ActionBar
        left={<HealthIndicator healthy={!blockers.length} label={blockers.length ? `${blockers.length} setup blockers` : 'Setup healthy'} />}
        right={<span>{jobsSummary.queued} jobs running/queued</span>}
      />
      <SectionPanel
        title="Workspace overview"
        description="Primary throughput, backlog, and publishing posture in one operator view."
        action={
          <Link href="/publishing" className="inline-flex items-center gap-1 text-sm font-medium text-[#2563eb]">
            Open publish queue
            <ArrowRight size={14} />
          </Link>
        }
      >
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.25fr)_360px]">
          <div className="space-y-4">
            <div className="rounded-[18px] border border-[#e5e7eb] bg-white p-5">
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#667085]">Control room</p>
              <h2 className="mt-3 text-2xl font-semibold tracking-[-0.03em] text-[#101828]">Run the business without the page fighting you.</h2>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-[#475467]">
                The dashboard now keeps the shell fixed and lets the section switcher change the operator view without dumping a second wall of controls above the fold.
              </p>
            </div>
            <div className="rounded-[18px] border border-[#dbe7ff] bg-[#f8fbff] p-5">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#667085]">Operator prompt</p>
                  <h3 className="mt-2 text-xl font-semibold tracking-[-0.03em] text-[#101828]">Type an operational request and preview it before it touches live eBay.</h3>
                  <p className="mt-2 max-w-2xl text-sm leading-6 text-[#475467]">
                    The first supported command is live eBay repricing by listing age. Example: lower all item prices by ten percent if they have been posted for more than 1 week on eBay.
                  </p>
                </div>
                <StatusPill status={setupSummary?.server_readiness?.openai_configured ? 'success' : 'default'} label={setupSummary?.server_readiness?.openai_configured ? 'OpenAI configured' : 'Rule-backed command mode'} />
              </div>
              <div className="mt-4 space-y-3">
                <textarea
                  value={operatorPrompt}
                  onChange={(event) => setOperatorPrompt(event.target.value)}
                  rows={4}
                  className="w-full rounded-[14px] border border-[#bfcce8] bg-white px-4 py-3 text-sm text-[#101828] shadow-sm outline-none transition focus:border-[#2563eb] focus:ring-2 focus:ring-[#bfdbfe]"
                  placeholder={DEFAULT_OPERATOR_PROMPT}
                />
                <div className="flex flex-wrap gap-2">
                  <Button onClick={() => runOperatorCommand({ applyLive: false })} disabled={operatorCommandRunning}>
                    {operatorCommandRunning ? 'Running preview...' : 'Preview command'}
                  </Button>
                  <Button variant="outline" onClick={() => setOperatorPrompt(DEFAULT_OPERATOR_PROMPT)} disabled={operatorCommandRunning}>
                    Load repricing example
                  </Button>
                </div>
                {operatorCommandResult ? (
                  <div className="rounded-[14px] border border-[#d0d5dd] bg-white p-4">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <p className="text-sm font-semibold text-[#101828]">
                          {operatorCommandResult.parsed ? 'Command preview' : 'Command not recognized'}
                        </p>
                        <p className="mt-1 text-sm text-[#667085]">{operatorCommandResult.message || 'No additional detail returned.'}</p>
                      </div>
                      {operatorCommandResult.parsed ? (
                        <StatusPill
                          status={operatorCommandResult.dry_run ? 'warning' : 'success'}
                          label={operatorCommandResult.dry_run ? 'Preview only' : 'Live applied'}
                        />
                      ) : null}
                    </div>
                    {operatorCommandResult.parsed ? (
                      <>
                        <div className="mt-4 grid gap-3 md:grid-cols-4">
                          <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
                            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Eligible</p>
                            <p className="mt-2 text-2xl font-semibold text-[#101828]">{operatorCommandResult.summary?.eligible_count || 0}</p>
                          </div>
                          <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
                            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Live eBay</p>
                            <p className="mt-2 text-2xl font-semibold text-[#101828]">{operatorCommandResult.summary?.total_live_ebay_listings || 0}</p>
                          </div>
                          <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
                            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Older than rule</p>
                            <p className="mt-2 text-2xl font-semibold text-[#101828]">{operatorCommandResult.summary?.older_than_threshold || 0}</p>
                          </div>
                          <div className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
                            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Updated</p>
                            <p className="mt-2 text-2xl font-semibold text-[#101828]">{operatorCommandResult.summary?.updated_count || 0}</p>
                          </div>
                        </div>
                        {operatorCommandResult.requires_confirmation ? (
                          <div className="mt-4 rounded-[12px] border border-[#fecdca] bg-[#fff6f3] p-4">
                            <p className="text-sm font-semibold text-[#912018]">Live eBay changes require explicit confirmation.</p>
                            <p className="mt-1 text-sm text-[#7a271a]">Type <span className="font-mono">{operatorCommandResult.confirmation_phrase}</span> before applying live price revisions.</p>
                            <div className="mt-3 flex flex-col gap-3 lg:flex-row">
                              <input
                                value={operatorConfirmation}
                                onChange={(event) => setOperatorConfirmation(event.target.value)}
                                className="min-w-0 flex-1 rounded-[12px] border border-[#fda29b] bg-white px-3 py-2 text-sm text-[#101828] outline-none focus:border-[#d92d20] focus:ring-2 focus:ring-[#fecdc9]"
                                placeholder={operatorCommandResult.confirmation_phrase}
                              />
                              <Button
                                onClick={() => runOperatorCommand({ applyLive: true })}
                                disabled={operatorCommandRunning || !operatorCommandResult.summary?.eligible_count}
                              >
                                {operatorCommandRunning ? 'Applying...' : 'Apply live eBay changes'}
                              </Button>
                            </div>
                          </div>
                        ) : null}
                        {operatorCommandResult.listings?.length ? (
                          <div className="mt-4 space-y-2">
                            {operatorCommandResult.listings.slice(0, 8).map((row) => (
                              <div key={row.listing_id} className="flex flex-wrap items-center justify-between gap-3 rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] px-4 py-3">
                                <div className="min-w-0">
                                  <p className="truncate text-sm font-medium text-[#101828]">{row.title || `Listing #${row.listing_id}`}</p>
                                  <p className="mt-1 text-xs text-[#667085]">#{row.listing_id}</p>
                                </div>
                                <div className="flex items-center gap-3 text-sm text-[#475467]">
                                  <span>{formatMoney(row.current_price)} to {formatMoney(row.new_price)}</span>
                                  <StatusPill status={row.status === 'failed' ? 'warning' : row.status === 'updated' ? 'success' : 'default'} label={row.status} />
                                </div>
                              </div>
                            ))}
                          </div>
                        ) : null}
                      </>
                    ) : null}
                  </div>
                ) : null}
              </div>
            </div>
            <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-4">
              {topMetrics.map((card) => (
                <MetricCard key={card.label} label={card.label} value={card.value} detail={card.detail} href={card.href} />
              ))}
            </div>
          </div>
          <div className="rounded-[18px] border border-[#e5e7eb] bg-[#fcfcfd] p-5">
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#667085]">Fast actions</p>
            <div className="mt-4 space-y-2">
              <Button href="/intake" variant="outline" className="w-full justify-between">
                <span>Upload photos</span>
                <ArrowRight size={14} />
              </Button>
              <Button href="/listings/new" className="w-full justify-between">
                <span>Create listing</span>
                <ArrowRight size={14} />
              </Button>
              <Button href="/publishing" variant="outline" className="w-full justify-between">
                <span>Open publish queue</span>
                <ArrowRight size={14} />
              </Button>
              <Button href="/settings?tab=ebay" variant="outline" className="w-full justify-between">
                <span>eBay setup</span>
                <ArrowRight size={14} />
              </Button>
            </div>
          </div>
        </div>
      </SectionPanel>
      {loadingPanels ? <LoadingSkeleton lines={5} /> : null}
    </div>
  );

  const renderActivity = () => (
    <div className="space-y-5">
      <DataTableCard
        title="Recent listing activity"
        description="Latest changes across drafts, review queue, and live marketplace rows."
        action={<Link href="/listings" className="text-sm font-medium text-[#2563eb]">View all listings</Link>}
        onRowClick={(listing) => router.push(`/listings/${listing.id}`)}
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
              <Link
                key={listing.id}
                href={`/listings/${listing.id}`}
                className="block rounded-[12px] border border-[#e5e7eb] bg-white px-4 py-3 transition hover:bg-[#f9fafb] focus:outline-none focus-visible:ring-2 focus-visible:ring-[#2563eb]"
              >
                <div className="flex items-start gap-3">
                  <AlertTriangle size={16} className="mt-0.5 shrink-0 text-[#b42318]" />
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-[#101828]">{listing.title || `Listing #${listing.id}`}</p>
                    <p className="mt-1 text-sm text-[#667085]">
                      {listing.ebay_publish_status === 'FAILED'
                        ? formatPublishFailureMessage(listing.marketplace_data?.error, 'ebay')
                        : 'Review publish or sync status before requeueing marketplace work.'}
                    </p>
                  </div>
                </div>
              </Link>
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
          {
            key: 'reference',
            label: 'Reference',
            render: (row) => {
              if (row.listing_id) {
                return (
                  <Link href={`/listings/${row.listing_id}`} className="font-medium text-[#2563eb]">
                    {row.reference}
                  </Link>
                );
              }
              return (
                <Link
                  href={`/jobs/${row.job_kind === 'imports' ? 'import' : 'crosspost'}/${row.job_id}`}
                  className="font-medium text-[#2563eb]"
                >
                  {row.reference}
                </Link>
              );
            },
          },
          { key: 'status', label: 'Status', render: (row) => <StatusPill status={row.status} label={row.status} /> },
          { key: 'updated_at', label: 'Updated', render: (row) => formatTime(row.updated_at) },
        ]}
        rows={recentJobRows}
        rowKey={(row) => row.id}
        emptyState={<EmptyState title="No jobs yet" description="Import and cross-post jobs will appear here once work starts moving through the marketplace execution layer." className="border-0 p-0 py-6" />}
      />
      <div className="grid gap-3 md:grid-cols-3">
        <MetricCard label="Queued or running" value={jobsSummary.queued} detail="Current job execution load." href="/jobs/active" />
        <MetricCard label="Completed" value={jobsSummary.completed} detail="Jobs that finished successfully." href="/jobs/completed" />
        <MetricCard label="Failed" value={jobsSummary.failed} detail="Jobs that need review or retry." href="/jobs/failed" />
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
                <Button
                  href={`/bridge-desktop?connectSessionId=${encodeURIComponent(activeBridgeConnectSession.connect_session_id)}`}
                  variant="outline"
                  size="sm"
                >
                  Resume Facebook login
                </Button>
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
      contentWidth="default"
    >
      <PageHeader
        eyebrow="Operations overview"
        breadcrumbs={[{ label: 'Workspace' }, { label: 'Dashboard', active: true }]}
        title="Reseller command center"
        description="One clean operator dashboard for intake, listing production, marketplace publishing, job flow, and account readiness."
        actions={
          <>
            {user?.can_access_vine_import ? (
              <Button variant="outline" onClick={() => vineFileInputRef.current?.click()} disabled={vineUploading}>
                <Upload size={16} />
                {vineUploading ? 'Uploading Vine report...' : 'Upload Vine report'}
              </Button>
            ) : null}
            <Button href="/intake" variant="outline">
              <Upload size={16} />
              Upload photos
            </Button>
            <Button href="/listings/new">
              <PlusCircle size={16} />
              Create listing
            </Button>
          </>
        }
      />
      {user?.can_access_vine_import ? (
        <input
          ref={vineFileInputRef}
          type="file"
          accept=".xlsx,.csv,.pdf"
          className="hidden"
          onChange={async (event) => {
            const file = event.target.files?.[0];
            if (!file) return;
            setVineUploading(true);
            try {
              const batch = await uploadVineReport(file);
              toast.success('Vine report uploaded. Opening import workspace.');
              router.push(`/imports/vine?batch=${encodeURIComponent(batch.id)}`);
            } catch (error) {
              toast.error(error.message);
            } finally {
              setVineUploading(false);
              event.target.value = '';
            }
          }}
        />
      ) : null}

      <div className="space-y-5">
        <SectionPanel
          title="Workspace sections"
          description="Switch between the main operator views without carrying a second sidebar around the page."
        >
          <div className="grid gap-3 xl:grid-cols-4">
            {dashboardSections.map((section) => {
              const active = activeSection === section.key;
              return (
                <button
                  key={section.key}
                  type="button"
                  onClick={() => selectSection(section.key)}
                  className={[
                    'rounded-[20px] border px-4 py-4 text-left transition',
                    active
                      ? 'border-[#bfd4ef] bg-[linear-gradient(135deg,#eef5ff_0%,#ffffff_100%)] text-[#173a63] shadow-[0_16px_32px_rgba(23,58,99,0.12)]'
                      : 'border-[#d0d5dd] bg-white text-[#344054] hover:border-[#98a2b3] hover:bg-[#f9fafb]',
                  ].join(' ')}
                >
                  <span className="block text-sm font-semibold">{section.label}</span>
                  <span className="mt-2 block text-xs leading-5 text-[#667085]">{section.description}</span>
                </button>
              );
            })}
          </div>
        </SectionPanel>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard label="Ready to publish" value={readyCount} detail="Listings that can move straight into marketplace publishing." href="/listings?tab=ready" />
          <MetricCard label="Pending review" value={reviewCount} detail="Drafts still waiting for operator approval." href="/listings?tab=review" />
          <MetricCard label="Live listings" value={liveCount} detail="Listings already posted or actively synced." href="/listings?tab=published" />
          <MetricCard label="Draft backlog" value={draftCount} detail="Items still moving through enrichment and manual edits." href="/listings?tab=drafts" />
        </div>
        {sectionContent[activeSection] || sectionContent.overview}
      </div>
    </AppShell>
  );
}

Dashboard.requireAuth = true;
