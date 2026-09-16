import Link from 'next/link';
import { useEffect, useMemo, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock3,
  Database,
  ListChecks,
  Package,
  PlusCircle,
  RefreshCcw,
  ShoppingCart,
  Store,
  Upload,
} from 'lucide-react';
import { useRouter } from 'next/router';

import AppShell from '../components/layout/AppShell';
import GuidedSetupCard from '../components/onboarding/GuidedSetupCard';
import ExtensionVersionStatus from '../components/marketplaces/ExtensionVersionStatus';
import GooglePhotosConnectionGuide from '../components/google/GooglePhotosConnectionGuide';
import ActionBar from '../components/ui/action-bar';
import Button from '../components/ui/button';
import CollapsiblePanel from '../components/ui/collapsible-panel';
import DataTableCard from '../components/ui/data-table-card';
import EmptyState from '../components/ui/empty-state';
import MetricCard from '../components/ui/metric-card';
import PageHeader from '../components/ui/page-header';
import Input from '../components/ui/input';
import Textarea from '../components/ui/textarea';
import Checkbox from '../components/ui/checkbox';
import QuickActionCard from '../components/ui/quick-action-card';
import StatusPill from '../components/ui/status-pill';
import HealthIndicator from '../components/ui/health-indicator';
import LoadingSkeleton from '../components/ui/loading-skeleton';
import SetupChecklistPanel from '../components/SetupChecklistPanel';
import { useAuth } from '../contexts/AuthContext';
import useDashboardData from '../hooks/useDashboardData';
import {
  fetchAccountSetupSummary,
  fetchAlerts,
  fetchIntakeQueue,
  fetchIntakeSettings,
  fetchMarketplaceJobsOverview,
  fetchMarketplaceExtensionDevices,
  fetchLatestMarketplaceDiagnostic,
  runDashboardOperatorCommand,
  runIntakeMonitor,
  fetchSalesDashboard,
  fetchSalesOperationsSummary,
  fetchGooglePhotosStatus,
  toggleAutonomousMode,
  setIntakeDraftingPaused,
  updateIntakeSettings,
  uploadVineReport,
  getGooglePhotosConnectUrl,
  startGooglePhotosOAuth,
  updateCurrentUser,
  updateServerSettings,
} from '../lib/api';

const DEFAULT_OPERATOR_PROMPT =
  'Lower all item prices by ten percent if they have been posted for more than 1 week on eBay.';
const DEFAULT_DASHBOARD_METRIC_LAYOUT = { order: ['ready', 'review', 'live', 'draft'], visible: { ready: true, review: true, live: true, draft: true } };

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
  const googlePhotosConnectUrl = getGooglePhotosConnectUrl();
  const googlePhotosRedirectUri =
    typeof window !== 'undefined'
      ? `${window.location.origin}/api/intake/google-photos/callback`
      : 'https://posterpro.sparkleserver.site/api/intake/google-photos/callback';
  const vineFileInputRef = useRef(null);
  const { listings, autonomousConfig, reload } = useDashboardData(user?.id, {
    includeListings: false,
    includeClusters: false,
    includeMarketplaces: false,
    includeAnalytics: false,
    includeAlerts: false,
    includeOfferDashboard: false,
    includePlatformConfig: false,
    includeStorageBatches: false,
    includeListingTemplates: false,
    // The dashboard only needs recent activity. Never serialize the entire
    // operator catalog here; the Listings workspace owns full pagination.
    paginateListings: true,
    listingPage: 1,
    listingPageSize: 25,
  });
  const [alerts, setAlerts] = useState([]);
  const [alertsAvailable, setAlertsAvailable] = useState(false);
  const [setupSummary, setSetupSummary] = useState(null);
  const [extensionState, setExtensionState] = useState({ devices: [], current_version: 'unknown', minimum_version: 'unknown' });
  const [browserExtensionDeviceId, setBrowserExtensionDeviceId] = useState(null);
  const [browserExtensionDetected, setBrowserExtensionDetected] = useState(false);
  const [browserExtensionVersion, setBrowserExtensionVersion] = useState('');
  const [jobsOverview, setJobsOverview] = useState({ import_jobs: [], crosspost_jobs: [], system_status: null });
  const [metricsUpdatedAt, setMetricsUpdatedAt] = useState(null);
  const [dashboardMetricLayout, setDashboardMetricLayout] = useState(DEFAULT_DASHBOARD_METRIC_LAYOUT);
  const [customizingMetrics, setCustomizingMetrics] = useState(false);
  const [showMetricDetails, setShowMetricDetails] = useState(false);
  const [salesDashboard, setSalesDashboard] = useState({ summary: {} });
  const [operationsSummary, setOperationsSummary] = useState({ shipping: { status: 'LOADING' }, messages: { status: 'LOADING' } });
  const [marketplaceDiagnostics, setMarketplaceDiagnostics] = useState({});
  const [googleStatus, setGoogleStatus] = useState(null);
  const [activeSection, setActiveSection] = useState('overview');
  const [vineUploading, setVineUploading] = useState(false);
  const [loadingPanels, setLoadingPanels] = useState(false);
  const [operatorPrompt, setOperatorPrompt] = useState(DEFAULT_OPERATOR_PROMPT);
  const [operatorConfirmationAcknowledged, setOperatorConfirmationAcknowledged] = useState(false);
  const [operatorCommandResult, setOperatorCommandResult] = useState(null);
  const [operatorCommandRunning, setOperatorCommandRunning] = useState(false);
  const [intakeSettings, setIntakeSettings] = useState(null);
  const [intakeQueue, setIntakeQueue] = useState({ batches: [], unassigned_photos: [], available: false });
  const [intakeAlbumUrl, setIntakeAlbumUrl] = useState('');
  const [intakeFolderId, setIntakeFolderId] = useState('');
  const [intakeSaving, setIntakeSaving] = useState(false);
  const [intakeSyncing, setIntakeSyncing] = useState(false);
  const systemStatus = jobsOverview.system_status || {};
  const metricsReady = Boolean(jobsOverview.system_status);
  const jobsSummaryReady = Boolean(jobsOverview.import_summary && jobsOverview.crosspost_summary);
  const salesSummaryReady = Object.prototype.hasOwnProperty.call(salesDashboard.summary || {}, 'gross') && Object.prototype.hasOwnProperty.call(salesDashboard.summary || {}, 'units');
  const googlePhotosConnected = Boolean(intakeSettings?.google_photos?.connected);
  const draftCount = Number(systemStatus.catalog_drafts || 0);
  const reviewCount = Number(systemStatus.catalog_review || 0);
  const liveCount = systemStatus.catalog_live ?? systemStatus.catalog_published ?? null;
  const liveByMarketplace = systemStatus.catalog_live_by_marketplace || {};
  const liveMarketplaceLabels = [
    ['ebay', 'eBay'], ['facebook', 'Facebook'], ['mercari', 'Mercari'],
    ['poshmark', 'Poshmark'], ['vinted', 'Vinted'], ['etsy', 'Etsy'], ['offerup', 'OfferUp'],
  ];
  const liveVerification = systemStatus.catalog_live_verification || 'LOCAL_LAST_KNOWN';
  const liveMarketplaceSummary = liveMarketplaceLabels
    .filter(([key]) => Number(liveByMarketplace[key]) > 0)
    .map(([key, label]) => `${label} ${liveByMarketplace[key]}`)
    .join(' · ');
  const readyCount = Number(systemStatus.catalog_ready || 0);
  const failedPublishCount = Number(systemStatus.catalog_failed || 0);

  useEffect(() => {
    const stored = user?.profile_preferences?.dashboard_metrics;
    if (!stored) { setDashboardMetricLayout(DEFAULT_DASHBOARD_METRIC_LAYOUT); return; }
    const valid = ['ready', 'review', 'live', 'draft'];
    const order = Array.isArray(stored.order) ? stored.order.filter((id) => valid.includes(id)) : [];
    setDashboardMetricLayout({
      order: [...new Set([...order, ...valid])],
      visible: Object.fromEntries(valid.map((id) => [id, stored.visible?.[id] !== false])),
    });
  }, [user?.id, user?.profile_preferences]);

  const saveDashboardMetricLayout = async (next) => {
    setDashboardMetricLayout(next);
    try { await updateCurrentUser({ profile_preferences: { dashboard_metrics: next } }); }
    catch (error) { toast.error(error.message || 'Dashboard layout could not be saved.'); }
  };
  const recentActivity = useMemo(
    () =>
      listings
        .slice()
        .sort((a, b) => new Date(b.updated_at || b.created_at || 0) - new Date(a.updated_at || a.created_at || 0))
        .slice(0, 7),
    [listings],
  );
  useEffect(() => {
    if (!user?.id) return;
    setLoadingPanels(true);
    Promise.allSettled([
      fetchMarketplaceJobsOverview({ limit: 25, compact: true }),
      fetchSalesDashboard(user.id, 25),
      fetchSalesOperationsSummary(),
      fetchAccountSetupSummary(user.id),
      fetchGooglePhotosStatus(),
      ...['facebook', 'mercari', 'poshmark', 'vinted', 'offerup'].map((market) => fetchLatestMarketplaceDiagnostic(market)),
    ]).then(([jobsResult, salesResult, operationsResult, setupResult, googleResult, ...diagnosticResults]) => {
      setJobsOverview(jobsResult.status === 'fulfilled' ? jobsResult.value || { import_jobs: [], crosspost_jobs: [] } : { import_jobs: [], crosspost_jobs: [] });
      if (jobsResult.status === 'fulfilled') setMetricsUpdatedAt(new Date().toISOString());
      setSalesDashboard(salesResult.status === 'fulfilled' ? salesResult.value || { summary: {} } : { summary: {} });
      setOperationsSummary(operationsResult.status === 'fulfilled' ? operationsResult.value : { shipping: { status: 'NEEDS_ATTENTION' }, messages: { status: 'NEEDS_ATTENTION' } });
      setSetupSummary(setupResult.status === 'fulfilled' ? setupResult.value : null);
      setGoogleStatus(googleResult.status === 'fulfilled' ? googleResult.value : null);
      setMarketplaceDiagnostics(Object.fromEntries(['facebook', 'mercari', 'poshmark', 'vinted', 'offerup'].map((market, index) => [market, diagnosticResults[index]?.status === 'fulfilled' ? diagnosticResults[index].value : null])));
      setLoadingPanels(false);
    });
  }, [user?.id]);

  useEffect(() => {
    if (!user?.id) return undefined;
    let active = true;
    const refreshSummary = () => fetchMarketplaceJobsOverview({ limit: 25, compact: true }).then((value) => {
      if (active && value) {
        setJobsOverview(value);
        setMetricsUpdatedAt(new Date().toISOString());
      }
    }).catch(() => {});
    const timer = window.setInterval(refreshSummary, 30000);
    return () => { active = false; window.clearInterval(timer); };
  }, [user?.id]);

  useEffect(() => {
    const receiveExtensionPresence = (event) => {
      if (event.source !== window || event.origin !== window.location.origin || event.data?.source !== 'posterpro-extension') return;
      if (event.data?.type === 'PRESENCE' || event.data?.type === 'AUTHORIZED') {
        setBrowserExtensionDetected(true);
        setBrowserExtensionVersion(String(event.data.version || ''));
      }
      const deviceId = Number(event.data.device_id || event.data.device?.id);
      if (Number.isInteger(deviceId) && deviceId > 0) setBrowserExtensionDeviceId(deviceId);
    };
    window.addEventListener('message', receiveExtensionPresence);
    window.postMessage({ source: 'posterpro-settings', type: 'CHECK_EXTENSION' }, window.location.origin);
    return () => window.removeEventListener('message', receiveExtensionPresence);
  }, []);

  useEffect(() => {
    if (!user?.id) return undefined;
    let active = true;
    const refreshExtension = () => fetchMarketplaceExtensionDevices().then((value) => { if (active) setExtensionState(value || { devices: [] }); }).catch(() => {});
    void refreshExtension();
    const timer = window.setInterval(refreshExtension, 15000);
    return () => { active = false; window.clearInterval(timer); };
  }, [user?.id]);

  useEffect(() => {
    let active = true;
    if (!user?.id) {
      setAlerts([]);
      setAlertsAvailable(false);
      return undefined;
    }
    fetchAlerts(user.id)
      .then((payload) => {
        if (active) { setAlerts(payload?.alerts || []); setAlertsAvailable(true); }
      })
      .catch(() => {
        if (active) { setAlerts([]); setAlertsAvailable(false); }
      });
    return () => {
      active = false;
    };
  }, [user?.id]);

  const allJobs = useMemo(() => [...(jobsOverview.import_jobs || []), ...(jobsOverview.crosspost_jobs || [])], [jobsOverview]);
  const jobsSummary = useMemo(
    () => ({
      queued: Number(jobsOverview.import_summary?.queued || 0) + Number(jobsOverview.import_summary?.running || 0)
        + Number(jobsOverview.crosspost_summary?.queued || 0) + Number(jobsOverview.crosspost_summary?.running || 0),
      failed: Number(jobsOverview.import_summary?.failed || 0) + Number(jobsOverview.crosspost_summary?.failed || 0),
      completed: Number(jobsOverview.import_summary?.completed || 0) + Number(jobsOverview.crosspost_summary?.completed || 0),
    }),
    [jobsOverview],
  );

  const blockers = useMemo(() => {
    if (!setupSummary) return null;
    const items = [];
    if (!setupSummary.account_profile_complete) items.push('Add an operator or business name.');
    if (!setupSummary.server_readiness?.ebay_oauth_configured) items.push('Server eBay OAuth credentials are still missing.');
    if (!setupSummary.server_readiness?.openai_configured) items.push('OpenAI is not configured for AI listing enrichment.');
    if (!setupSummary.server_readiness?.photoroom_configured) items.push('PhotoRoom is not configured for background removal.');
    if (!setupSummary.marketplace_connections?.some((item) => item.connected)) items.push('No marketplace account is connected yet.');
    return items;
  }, [setupSummary]);

  const marketplaceWidgets = (setupSummary?.marketplace_connections || []).slice(0, 6);
  const activeBridgeConnectSession = setupSummary?.active_bridge_connect_session || null;
  const browserAssistPromptTargets = useMemo(() => {
    const browserTargets = ['facebook', 'mercari'];
    return (setupSummary?.marketplace_connections || [])
      .filter((connection) => browserTargets.includes(String(connection.marketplace || '').toLowerCase()))
      .filter((connection) => !connection.connected || !connection.bridge_account_key);
  }, [setupSummary]);
  const showBrowserAssistPrompt = Boolean(browserAssistPromptTargets.length || activeBridgeConnectSession);
  const topMetrics = [
    { id: 'ready', label: 'Ready to publish', value: metricsReady ? readyCount : 'Not available', detail: 'Approved listings without a live marketplace copy.', href: '/listings?tab=ready' },
    { id: 'review', label: 'Pending review', value: metricsReady ? reviewCount : 'Not available', detail: 'Current Needs Review queue across the full catalog.', href: '/listings?tab=review' },
    { id: 'live', label: 'Live listings', value: metricsReady && liveCount != null ? Number(liveCount) : 'Not available', detail: metricsReady ? `${liveMarketplaceSummary || 'No confirmed marketplace copies'}${liveVerification === 'STALE_REMOTE_SNAPSHOT' ? ' · eBay check is stale' : liveVerification === 'UNAVAILABLE' ? ' · eBay check unavailable' : ''}` : 'Marketplace counts unavailable.', href: '/listings?tab=published' },
    { id: 'draft', label: 'Draft backlog', value: metricsReady ? draftCount : 'Not available', detail: 'Unfinished listings not yet ready for human review.', href: '/listings?tab=drafts' },
  ];
  const orderedMetrics = dashboardMetricLayout.order.map((id) => topMetrics.find((metric) => metric.id === id)).filter((metric) => metric && dashboardMetricLayout.visible[metric.id]);
  const reorderDashboardMetrics = (draggedId, targetId) => {
    if (!draggedId || draggedId === targetId) return;
    const order = [...dashboardMetricLayout.order];
    const from = order.indexOf(draggedId);
    const to = order.indexOf(targetId);
    if (from < 0 || to < 0) return;
    order.splice(from, 1);
    order.splice(to, 0, draggedId);
    void saveDashboardMetricLayout({ ...dashboardMetricLayout, order });
  };
  const workspaceStats = [
    { label: 'Batch jobs', value: jobsSummaryReady ? jobsSummary.queued : 'Not available', note: 'Queued or running, across all jobs' },
    { label: 'Failures', value: jobsSummaryReady ? jobsSummary.failed + failedPublishCount : 'Not available', note: 'Failed jobs plus unpublished failed listings' },
    { label: 'Sales gross', value: salesSummaryReady ? `$${Number(salesDashboard.summary.gross || 0).toFixed(0)}` : 'Not available', note: 'Detected channel revenue' },
    { label: 'Units sold', value: salesSummaryReady ? salesDashboard.summary.units : 'Not available', note: 'Completed units' },
  ];
  const readinessRows = [
    ['OpenAI', setupSummary?.server_readiness?.openai_configured, 'AI enrichment and pricing help'],
    ['PhotoRoom', setupSummary?.server_readiness?.photoroom_configured, 'Image cleanup workflows'],
    ['eBay OAuth', setupSummary?.server_readiness?.ebay_oauth_configured, 'Direct eBay publishing'],
    ['Google Photos OAuth', setupSummary?.server_readiness?.google_photos_oauth_configured, 'Slate upload authorization'],
    ['Session security', setupSummary?.server_readiness?.session_secret_configured, 'Encrypted secret handling'],
  ];
  const intakeMetrics = useMemo(() => {
    const batches = intakeQueue?.batches || [];
    return {
      batches: batches.length,
      drafted: batches.filter((item) => item.draft_listing_id).length,
      ready: batches.filter((item) => item.status === 'ready_for_draft').length,
      unassigned: (intakeQueue?.unassigned_photos || []).length,
    };
  }, [intakeQueue]);
  const intakeLastRun = intakeSettings?.last_monitor_result || null;
  const latestDraftedBatch = useMemo(
    () => (intakeQueue?.batches || []).find((item) => item.draft_listing_id) || null,
    [intakeQueue],
  );
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

  const saveIntakeAlbum = async () => {
    setIntakeSaving(true);
    try {
      const payload = await updateIntakeSettings({
        ...(intakeSettings || {}),
        enabled: true,
        album_url: intakeAlbumUrl || null,
        folder_id: intakeFolderId || null,
      });
      setIntakeSettings(payload);
      setIntakeAlbumUrl(payload?.album_url || '');
      setIntakeFolderId(payload?.folder_id || '');
      toast.success('Intake album settings saved.');
    } catch (error) {
      toast.error(error.message || 'Failed to save intake album settings.');
    } finally {
      setIntakeSaving(false);
    }
  };

  const toggleIntakeDraftingPause = async (paused) => {
    if (!intakeSettings) return;
    setIntakeSaving(true);
    try {
      const payload = await setIntakeDraftingPaused(intakeSettings, paused);
      setIntakeSettings(payload);
      setIntakeAlbumUrl(payload?.album_url || '');
      setIntakeFolderId(payload?.folder_id || '');
      toast.success(paused ? 'Drafting paused.' : 'Drafting resumed.');
    } catch (error) {
      toast.error(error.message || 'Failed to update drafting state.');
    } finally {
      setIntakeSaving(false);
    }
  };

  const runDashboardIntakeMonitor = async () => {
    setIntakeSyncing(true);
    try {
      const payload = await runIntakeMonitor();
      setIntakeSettings(payload?.settings || intakeSettings);
      const latestQueue = await fetchIntakeQueue();
      setIntakeQueue({
        batches: latestQueue?.batches || [],
        unassigned_photos: latestQueue?.unassigned_photos || [],
      });
      toast.success(`Intake sync finished: ${payload?.result?.imported || 0} imported, ${payload?.result?.drafts_created || 0} drafts created.`);
    } catch (error) {
      toast.error(error.message || 'Failed to run intake monitor.');
    } finally {
      setIntakeSyncing(false);
    }
  };

  const startGoogleLogin = async () => {
    try {
      const authPayload = await startGooglePhotosOAuth();
      if (authPayload?.auth_url) {
        window.location.assign(authPayload.auth_url);
        return;
      }
      throw new Error('Google Photos OAuth URL was not returned by the server.');
    } catch (error) {
      if (String(error?.message || '').toLowerCase().includes('missing google photos oauth client settings')) {
        router.push('/settings/intake?google_photos=missing-config#google-photos-oauth');
        return;
      }
      toast.error(error.message || 'Unable to start Google login.');
    }
  };

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
    if (applyLive && !operatorConfirmationAcknowledged) {
      toast.error('Confirm the live checkbox before applying changes.');
      return;
    }
    setOperatorCommandRunning(true);
    try {
      const result = await runDashboardOperatorCommand({
        prompt: operatorPrompt,
        dry_run: !applyLive,
        apply_live: applyLive,
        confirm_live_apply: applyLive ? operatorConfirmationAcknowledged : false,
      }, applyLive ? { timeoutMs: 300000 } : {});
      setOperatorCommandResult(result);
      if (applyLive) {
        toast.success(result?.message || 'Live operator command finished.');
        setOperatorConfirmationAcknowledged(false);
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
      <GuidedSetupCard />
      {showBrowserAssistPrompt ? (
        <div className="rounded-[18px] border border-[#dbe7ff] bg-[linear-gradient(135deg,#f8fbff_0%,#ffffff_100%)] p-5 shadow-[0_12px_32px_rgba(15,23,42,0.06)]">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="max-w-3xl">
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#667085]">Browser connection</p>
              <h2 className="mt-2 text-xl font-semibold tracking-[-0.03em] text-[#101828]">Connect the browser you already use.</h2>
              <p className="mt-2 text-sm leading-6 text-[#475467]">
                PosterPro works with marketplace accounts already signed in to Chrome or Edge. Your passwords stay in your browser.
              </p>
              {browserAssistPromptTargets.length ? (
                <p className="mt-2 text-sm text-[#344054]">
                  Needs setup for: <span className="font-medium text-[#101828]">{browserAssistPromptTargets.map((item) => item.display_name || item.marketplace).join(', ')}</span>
                </p>
              ) : null}
            </div>
            <div className="flex flex-wrap gap-2">
              <Button href="/onboarding">
                Continue guided setup
              </Button>
              {activeBridgeConnectSession ? (
                <Button href={`/bridge-desktop?connectSessionId=${encodeURIComponent(activeBridgeConnectSession.connect_session_id)}`}>
                  Resume Facebook login
                </Button>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
      <ActionBar
        left={<HealthIndicator healthy={Boolean(setupSummary) && !blockers?.length} label={!setupSummary ? 'Setup status unavailable' : blockers.length ? `${blockers.length} setup items need attention` : 'Setup healthy'} />}
        right={<span>{jobsSummaryReady ? `${jobsSummary.queued} jobs running/queued` : 'Job status unavailable'}</span>}
      />
      <CollapsiblePanel title="Head Slate intake" description="Connect or run the photo intake workflow when you need it." defaultOpen={false}>
        <div className="mb-4">
          <GooglePhotosConnectionGuide
            connected={googlePhotosConnected}
            accountLabel={intakeSettings?.google_photos?.account_email || intakeSettings?.google_photos?.account_name || intakeSettings?.google_photos?.account_subject}
            albumLabel={intakeSettings?.album_url || intakeSettings?.folder_id || 'PosterPro'}
            albumId={intakeSettings?.google_photos?.album_id || intakeSettings?.google_photos?.album_identifier}
            connectionState={intakeSettings?.google_photos?.connection_state}
            redirectUri={intakeSettings?.google_photos?.redirect_uri || googlePhotosRedirectUri}
            connectUrl={googlePhotosConnectUrl}
            apiKeysUrl="/settings/intake?google_photos=missing-config#google-photos-oauth"
            slateUrl="/intake/slate"
            onRefresh={async () => {
              try {
                const nextIntakeSettings = await fetchIntakeSettings();
                setIntakeSettings(nextIntakeSettings || null);
                toast.success('Google Photos status refreshed.');
              } catch (error) {
                toast.error(error.message || 'Failed to refresh Google Photos status.');
              }
            }}
            onStartLogin={startGoogleLogin}
            missingConfig={!Boolean(intakeSettings?.google_photos?.connected) && !Boolean(intakeSettings?.google_photos?.account_email || intakeSettings?.google_photos?.account_name || intakeSettings?.google_photos?.account_subject)}
            compact
          />
        </div>
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(320px,0.8fr)]">
          <div className="rounded-[18px] border border-[#e5e7eb] bg-white p-5">
            {!googlePhotosConnected ? (
              <div className="mb-4 rounded-[18px] border border-red-200 bg-red-50 p-4 text-red-900">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold uppercase tracking-[0.16em]">Google Photos not connected</p>
                    <p className="mt-1 text-sm text-red-800">Authorize PosterPro with Google before expecting automatic slate upload.</p>
                  </div>
                  <Button onClick={startGoogleLogin} variant="secondary"><Upload size={16} /> Connect Google Photos</Button>
                </div>
              </div>
            ) : null}
            <div className="flex flex-wrap items-center gap-2">
              <StatusPill status={intakeSettings?.enabled ? 'success' : 'warning'} label={intakeSettings?.enabled ? 'Monitor enabled' : 'Monitor disabled'} />
              <StatusPill status={intakeSettings?.drafting_paused ? 'warning' : 'success'} label={intakeSettings?.drafting_paused ? 'Drafting paused' : 'Drafting active'} />
              <StatusPill status={intakeMetrics.unassigned ? 'warning' : intakeMetrics.ready || intakeMetrics.drafted ? 'success' : 'default'} label={intakeMetrics.unassigned ? 'Needs slate grouping' : 'Stable'} />
            </div>
            <label className="mt-4 block">
              <span className="text-sm font-semibold text-[#101828]">Google Photos album URL</span>
              <div className="mt-2 grid gap-3">
              <Input
                value={intakeAlbumUrl}
                onChange={(event) => setIntakeAlbumUrl(event.target.value)}
                className="w-full"
                  placeholder="https://photos.app.goo.gl/... or a shared Drive link"
                />
                <Input
                  value={intakeFolderId}
                  onChange={(event) => setIntakeFolderId(event.target.value)}
                  className="w-full"
                  placeholder="Optional Google Drive folder URL or shared link"
                />
              </div>
            </label>
            <div className="mt-4 flex flex-wrap gap-2">
              <Button onClick={saveIntakeAlbum} disabled={intakeSaving || !(intakeAlbumUrl.trim() || intakeFolderId.trim())}>
                {intakeSaving ? 'Saving…' : 'Save source + enable monitor'}
              </Button>
              <Button onClick={() => toggleIntakeDraftingPause(true)} variant="outline" disabled={intakeSaving || intakeSettings?.drafting_paused}>
                Pause drafting
              </Button>
              <Button onClick={() => toggleIntakeDraftingPause(false)} variant="secondary" disabled={intakeSaving || !intakeSettings?.drafting_paused}>
                Resume drafting
              </Button>
              <Button variant="secondary" onClick={runDashboardIntakeMonitor} disabled={intakeSyncing || !googlePhotosConnected}>
                {intakeSyncing ? 'Running…' : 'Run intake now'}
              </Button>
              <Button href="/intake/slate" variant="outline">Generate head slate</Button>
            </div>
            <p className="mt-3 text-sm text-[#667085]">
              When a head slate is detected, PosterPro groups the following photos into one item batch, builds the draft, and sends it into review.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              {(intakeSettings?.marketplace_defaults?.targets || ['ebay', 'facebook']).map((target) => (
                <span key={target} className="pp-chip capitalize">{target}</span>
              ))}
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1">
            <div className="rounded-[18px] border border-[#e5e7eb] bg-[#fcfcfd] p-4">
              <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Queued intake batches</p>
              <p className="mt-2 text-2xl font-semibold text-[#101828]">{intakeQueue.available ? intakeMetrics.batches : 'Not available'}</p>
              <p className="mt-1 text-sm text-[#667085]">{intakeMetrics.ready} ready to draft, {intakeMetrics.drafted} drafted</p>
            </div>
            <div className="rounded-[18px] border border-[#e5e7eb] bg-[#fcfcfd] p-4">
              <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Unassigned photos</p>
              <p className="mt-2 text-2xl font-semibold text-[#101828]">{intakeQueue.available ? intakeMetrics.unassigned : 'Not available'}</p>
              <p className="mt-1 text-sm text-[#667085]">{intakeMetrics.unassigned ? 'Photos imported without a matching slate boundary.' : 'Album stream is grouped cleanly.'}</p>
            </div>
            <div className="rounded-[18px] border border-[#e5e7eb] bg-[#fcfcfd] p-4">
              <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Last sync</p>
              <p className="mt-2 text-sm font-semibold text-[#101828]">{formatTime(intakeSettings?.last_synced_at)}</p>
              <p className="mt-1 text-sm text-[#667085]">{intakeSettings?.last_error || 'No intake monitor errors reported.'}</p>
            </div>
            {intakeLastRun ? (
              <div className="rounded-[18px] border border-[#dbe7ff] bg-[#f8fbff] p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Last monitor run</p>
                <p className="mt-2 text-sm font-semibold text-[#101828]">
                  {intakeLastRun.imported || 0} imported, {intakeLastRun.slates_detected || 0} slates, {intakeLastRun.assigned_photos || 0} assigned, {intakeLastRun.drafts_created || 0} drafts.
                </p>
                <p className="mt-1 text-sm text-[#667085]">
                  Scanned {intakeLastRun.scanned || 0} photos with {intakeLastRun.failed_downloads || 0} download failures.
                </p>
              </div>
            ) : null}
            {latestDraftedBatch?.draft_listing_id ? (
              <Button href={`/listings/${latestDraftedBatch.draft_listing_id}?mode=preview`} variant="outline" className="justify-between">
                <span>Preview latest intake draft</span>
                <ArrowRight size={14} />
              </Button>
            ) : null}
            <Button href={intakeMetrics.unassigned ? '/intake/queue' : '/listings?tab=review'} variant="outline" className="justify-between">
              <span>{intakeMetrics.unassigned ? 'Open intake queue' : 'Open review queue'}</span>
              <ArrowRight size={14} />
            </Button>
          </div>
        </div>
      </CollapsiblePanel>
      <CollapsiblePanel
        title="Workspace overview"
        description="Optional operator tools for intake and eBay repricing."
        defaultOpen={false}
        badge={metricsReady ? `${readyCount} ready` : 'Loading catalog summary'}
        action={
          <Link href="/publishing" className="inline-flex items-center gap-1 text-sm font-medium text-[#2563eb]">
            Open publish queue
            <ArrowRight size={14} />
          </Link>
        }
      >
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1.25fr)_360px]">
          <div className="space-y-4">
            <div className="rounded-[18px] border border-[#dbe7ff] bg-[#f8fbff] p-5">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#667085]">Operator prompt</p>
                  <h3 className="mt-2 text-xl font-semibold tracking-[-0.03em] text-[#101828]">Type an operational request and preview it before it touches live eBay.</h3>
                  <p className="mt-2 max-w-2xl text-sm leading-6 text-[#475467]">
                    Natural-language repricing is supported for live eBay listings. Example: reduce all item prices by 10% if they are above $50, or lower published eBay prices by 10% for listings older than 1 week.
                  </p>
                </div>
                <StatusPill status={setupSummary?.server_readiness?.openai_configured ? 'success' : 'default'} label={setupSummary?.server_readiness?.openai_configured ? 'OpenAI configured' : 'Rule-backed command mode'} />
              </div>
              <div className="mt-4 space-y-3">
                <Textarea
                  value={operatorPrompt}
                  onChange={(event) => setOperatorPrompt(event.target.value)}
                  rows={4}
                  className="w-full"
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
                        <div className="mt-4 grid gap-3 lg:grid-cols-4">
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
                            <p className="mt-1 text-sm text-[#7a271a]">Confirm the checkbox below, then apply the live price changes.</p>
                            <div className="mt-3 flex flex-col gap-3 lg:flex-row lg:items-center">
                              <Checkbox
                                className="flex-1"
                                checked={operatorConfirmationAcknowledged}
                                onChange={(event) => setOperatorConfirmationAcknowledged(event.target.checked)}
                                label="I understand this will queue real live eBay price changes for the eligible listings."
                              />
                              <Button
                                onClick={() => runOperatorCommand({ applyLive: true })}
                                disabled={operatorCommandRunning || !operatorCommandResult.summary?.eligible_count || !operatorConfirmationAcknowledged}
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
          </div>
          <CollapsiblePanel title="Fast actions" description="Most common workflow moves kept behind a single task panel." defaultOpen={false}>
            <div className="rounded-[18px] border border-[#e5e7eb] bg-[#fcfcfd] p-5">
              <div className="space-y-2">
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
          </CollapsiblePanel>
        </div>
      </CollapsiblePanel>
      {loadingPanels ? <LoadingSkeleton lines={5} /> : null}
    </div>
  );

  const renderActivity = () => (
    <div className="space-y-5">
      <CollapsiblePanel title="Recent listing activity" description="Latest changes across drafts, review queue, and live marketplace rows." defaultOpen>
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
      </CollapsiblePanel>

      <CollapsiblePanel title="Attention feed" description="Errors, alerts, and items that need an operator now." defaultOpen={false}>
        <div className="space-y-3">
          {alerts?.length ? (
            alerts.slice(0, 5).map((alert, index) => (
              alert.href ? (
                <Link key={`${alert.title || 'alert'}-${index}`} href={alert.href} className="block rounded-[12px] border border-[#e5e7eb] bg-white px-4 py-3 transition hover:bg-[#f9fafb]">
                  <p className="text-sm font-medium text-[#101828]">{alert.title || 'Alert'}</p>
                  <p className="mt-1 text-sm text-[#667085]">{alert.message || 'Check the latest workflow state.'}</p>
                </Link>
              ) : (
                <div key={`${alert.title || 'alert'}-${index}`} className="rounded-[12px] border border-[#e5e7eb] bg-white px-4 py-3">
                  <p className="text-sm font-medium text-[#101828]">{alert.title || 'Alert'}</p>
                  <p className="mt-1 text-sm text-[#667085]">{alert.message || 'Check the latest workflow state.'}</p>
                </div>
              )
            ))
          ) : (
            <div className="rounded-[12px] border border-[#e5e7eb] bg-white px-4 py-8 text-center">
              {alertsAvailable ? <Clock3 size={18} className="mx-auto text-[#2563eb]" /> : <AlertTriangle size={18} className="mx-auto text-[#b54708]" />}
              <p className="mt-3 text-sm font-medium text-[#101828]">{alertsAvailable ? 'No urgent events right now' : 'Alerts are not available'}</p>
              <p className="mt-1 text-sm text-[#667085]">{alertsAvailable ? 'Failures, alerts, and queue issues will surface here.' : 'PosterPro could not verify the current alert list. Try refreshing the Dashboard.'}</p>
            </div>
          )}
        </div>
      </CollapsiblePanel>
    </div>
  );

  const renderJobs = () => (
    <div className="space-y-5">
      <CollapsiblePanel title="Live system status" description={systemStatus.status_message || 'Current intake and job-processing state.'} defaultOpen>
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
          <MetricCard label="Intake active" value={metricsReady ? systemStatus.intake_batches_active + systemStatus.intake_photos_processing : 'Not available'} detail="Batches and photos still moving through intake." />
          <MetricCard label="Queued work" value={metricsReady ? systemStatus.queued_jobs + systemStatus.running_jobs : 'Not available'} detail="Worker tasks currently waiting or executing." href="/jobs/active" />
          <MetricCard label="Failed work" value={metricsReady ? systemStatus.failed_jobs : 'Not available'} detail="Jobs that need attention or retry." href="/jobs/failed" />
          <MetricCard label="Unread notices" value={metricsReady ? systemStatus.unread_notifications : 'Not available'} detail="Process updates waiting for review." />
        </div>
      </CollapsiblePanel>
      <CollapsiblePanel title="Batch processing status" description="Recent import and cross-post jobs without leaving the dashboard." defaultOpen>
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
          emptyState={<EmptyState title={jobsSummaryReady ? 'No jobs yet' : 'Job status unavailable'} description={jobsSummaryReady ? 'Import and cross-post jobs will appear here once work starts moving through the marketplace execution layer.' : 'PosterPro could not verify the current jobs list.'} className="border-0 p-0 py-6" />}
        />
      </CollapsiblePanel>
    </div>
  );

  const renderOperations = () => (
    <div className="space-y-5">
      <CollapsiblePanel title="Operational posture" description="Short-form status modules instead of one oversized narrative dashboard." defaultOpen>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <div className="rounded-[14px] border border-[#e5e7eb] bg-white p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Marketplace sync</p>
            <div className="mt-3">
              <StatusPill status={!metricsReady ? 'default' : failedPublishCount ? 'warning' : 'success'} label={!metricsReady ? 'Status unavailable' : failedPublishCount ? 'Attention needed' : 'Healthy'} />
            </div>
            <p className="mt-2 text-sm text-[#667085]">Derived from current publish failures and live listing state.</p>
          </div>
          <div className="rounded-[14px] border border-[#e5e7eb] bg-white p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Automation mode</p>
            <div className="mt-3">
              <StatusPill status={autonomousConfig?.loaded ? (autonomousConfig.autonomous_mode ? 'success' : 'default') : 'default'} label={!autonomousConfig?.loaded ? 'Status unavailable' : autonomousConfig.autonomous_mode ? 'Automation on' : 'Automation off'} />
            </div>
            <p className="mt-2 text-sm text-[#667085]">Controls how aggressively drafts advance without human intervention.</p>
          </div>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {workspaceStats.map((item) => (
            <div key={item.label} className="rounded-[14px] border border-[#e5e7eb] bg-[#fcfcfd] p-4">
              <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">{item.label}</p>
              <p className="mt-2 text-2xl font-semibold text-[#101828]">{item.value}</p>
              <p className="mt-1 text-sm text-[#667085]">{item.note}</p>
            </div>
          ))}
        </div>
      </CollapsiblePanel>

      <CollapsiblePanel title="Quick actions" description="Most common workflow moves kept in a dedicated command module." defaultOpen={false}>
        <div className="grid gap-3 xl:grid-cols-2">
          <QuickActionCard href="/intake" icon={Upload} eyebrow="Intake" title="Upload Photos" description="Start a new intake batch with loose photos or a zip import." meta={metricsReady ? `${systemStatus.catalog_total} items` : 'Catalog unavailable'} />
          <QuickActionCard href="/listings/new" icon={PlusCircle} eyebrow="Listings" title="Create Listing" description="Open the listing workspace directly for a manual item or imported draft." meta={metricsReady ? `${draftCount} drafts` : 'Draft count unavailable'} />
          <QuickActionCard href="/publishing" icon={RefreshCcw} eyebrow="Publishing" title="Publish Queue" description="Review approvals, queue health, and live marketplace rows." meta={metricsReady ? `${readyCount} ready` : 'Queue count unavailable'} />
          <QuickActionCard href="/sales" icon={ShoppingCart} eyebrow="Revenue" title="Sales & Orders" description="Monitor detected sales and finish bookkeeping adjustments." meta={salesSummaryReady ? `${salesDashboard.summary.units} units` : 'Sales summary unavailable'} />
        </div>
      </CollapsiblePanel>
    </div>
  );

  const renderChannels = () => (
    <div className="space-y-5">
      <CollapsiblePanel title="Marketplace connections" description="Channel state visible as a compact admin panel instead of a dashboard sidebar fragment." defaultOpen>
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
            <div className="grid gap-3 xl:grid-cols-2">
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
      </CollapsiblePanel>
    </div>
  );

  const renderSetup = () => (
    <div className="space-y-5">
      {setupSummary ? <SetupChecklistPanel setupSummary={setupSummary} /> : null}
      <CollapsiblePanel title="Current blockers" description="Missing setup or workflow conditions that still prevent clean end-to-end operation." defaultOpen>
        <div className="space-y-3">
          {!setupSummary ? (
            <div className="rounded-[12px] border border-[#e5e7eb] bg-[#f8fafc] px-4 py-5 text-sm text-[#475467]">Setup status is not available right now. Retry from Guided Setup.</div>
          ) : blockers.length ? (
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
      </CollapsiblePanel>
    </div>
  );

  const renderSystem = () => (
    <div className="space-y-5">
      <CollapsiblePanel title="System readiness" description="Live dependencies that still control automation depth." defaultOpen>
        <div className="grid gap-3 xl:grid-cols-2">
          {readinessRows.map(([label, ok, note]) => (
            <div key={label} className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <span className="inline-flex h-10 w-10 items-center justify-center rounded-[12px] bg-[#f8fafc] text-[#2563eb]">
                    <Database size={16} />
                  </span>
                  <p className="text-sm font-semibold text-[#101828]">{label}</p>
                </div>
                <StatusPill status={setupSummary && typeof ok === 'boolean' ? (ok ? 'success' : 'warning') : 'default'} label={!setupSummary || typeof ok !== 'boolean' ? 'Not available' : ok ? 'Ready' : 'Missing'} />
              </div>
              <p className="mt-2 text-sm text-[#667085]">{note}</p>
            </div>
          ))}
        </div>
      </CollapsiblePanel>
      <div className="grid gap-3 xl:grid-cols-2">
        <QuickActionCard href="/settings?tab=ebay" icon={Store} eyebrow="Integrations" title="Connect eBay" description="Finish OAuth setup or reconnect the current operator account." meta={!setupSummary ? 'Status unavailable' : setupSummary.server_readiness?.ebay_oauth_configured ? 'Configured' : 'Needs setup'} />
        <QuickActionCard href="/inventory" icon={Package} eyebrow="Inventory" title="View Inventory" description="Inspect intake, active items, sold units, and storage batches." meta={metricsReady ? `${systemStatus.catalog_total} tracked` : 'Catalog unavailable'} />
      </div>
    </div>
  );

  const renderDashboardSummary = () => {
    const alertsByType = alerts.reduce((groups, alert) => {
      const type = String(alert.type || '').toLowerCase();
      const key = type.includes('stale') ? 'stale' : ['overpriced', 'high_demand', 'market_price_drop'].includes(type) ? 'pricing' : type.includes('intake') ? 'intake' : 'other';
      groups[key] = (groups[key] || 0) + Math.max(1, Number(alert.count || 1));
      return groups;
    }, {});
    const marketplaceStatus = (market) => {
      const diagnostic = marketplaceDiagnostics[market];
      if (!diagnostic || diagnostic.status === 'NOT_RUN') return 'Test required';
      if (diagnostic.result?.capability_ready === true) return 'Form test passed';
      if (diagnostic.result?.login_state === 'LOGIN_REQUIRED') return 'Sign in required';
      if (['FAILED', 'BLOCKED_EXTERNAL'].includes(diagnostic.status)) return 'Needs attention';
      return 'Test required';
    };
    const shipping = operationsSummary.shipping || {};
    const message = operationsSummary.messages || {};
    const eBayConnection = ['REMOTE_VERIFIED', 'PARTIAL_REMOTE_RESULT'].includes(shipping.status) || ['REMOTE_VERIFIED', 'PARTIAL_REMOTE_RESULT'].includes(message.status)
      ? 'Connected · live check' : shipping.status === 'AUTH_REQUIRED' || message.status === 'AUTH_REQUIRED' ? 'Reconnect required' : shipping.status === 'NOT_CONFIGURED' ? 'Not connected' : 'Check required';
    const googleConnection = googleStatus?.connection_state === 'connected' && googleStatus?.connected ? 'Connected' : googleStatus?.connection_state === 'token_expired' || googleStatus?.connection_state === 'revoked' ? 'Reconnect required' : googleStatus ? 'Needs setup' : 'Not checked';
    const aiSetup = setupSummary?.ai || {};
    const aiVerifiedAt = aiSetup.verified_at ? new Date(aiSetup.verified_at).getTime() : 0;
    const aiVerificationRecent = aiVerifiedAt > 0 && Date.now() - aiVerifiedAt < 30 * 24 * 60 * 60 * 1000;
    const aiConnection = aiSetup.state === 'COMING_SOON' ? 'Premium AI coming soon'
      : aiSetup.mode === 'BYO_OPENAI' && aiSetup.state === 'CONNECTED' && aiVerificationRecent ? 'Connected · recently tested'
        : aiSetup.byo_configured || aiSetup.state === 'CONNECTED' ? 'Test required' : 'Not configured';
    const groupedAlerts = [
      ['stale', 'Listings older than 30 days', '/inventory?stale=true'],
      ['pricing', 'Pricing review', '/listings?tab=review'],
      ['intake', 'Intake needs attention', '/intake/queue'],
      ['other', 'Other listing issues', '/notices'],
    ].filter(([key]) => alertsByType[key]);
    return (
      <div className="pp-dashboard">
        <ExtensionVersionStatus state={extensionState} currentDeviceId={browserExtensionDeviceId} detected={browserExtensionDetected} detectedVersion={browserExtensionVersion} compact />
        <header className="pp-dashboard-header">
          <h1 className="pp-dashboard-title">Dashboard</h1>
          <div className="pp-dashboard-actions">
            <Button variant="tertiary" onClick={() => setCustomizingMetrics((value) => !value)}>{customizingMetrics ? 'Done customizing' : 'Customize'}</Button>
            <Button href="/intake"><Upload size={17} aria-hidden="true" />Start intake</Button>
            <Button href="/listings?tab=review" variant="secondary"><ListChecks size={17} aria-hidden="true" />Review listings</Button>
          </div>
        </header>
        <section aria-label="Listing metrics" className="pp-dashboard-metrics">
          {orderedMetrics.map((card) => <MetricCard key={card.id} className="pp-dashboard-metric" label={card.id === 'ready' ? 'Ready' : card.id === 'review' ? 'Needs review' : card.id === 'live' ? 'Live' : 'Draft'} value={card.value} detail={card.id === 'live' ? liveVerification === 'REMOTE_VERIFIED' ? 'eBay verified' : 'See marketplace status' : undefined} href={card.href} />)}
        </section>
        {dashboardMetricLayout.visible.live !== false ? (
          <section className="pp-dashboard-card" aria-label="Live listings by marketplace">
            <div className="pp-dashboard-card__header"><h2 className="pp-dashboard-card__title">Live by marketplace</h2><span className="pp-dashboard-meta">{liveVerification === 'REMOTE_VERIFIED' ? 'eBay checked live' : liveVerification === 'STALE_REMOTE_SNAPSHOT' ? 'eBay last known' : 'Marketplace check unavailable'}</span></div>
            <div className="pp-dashboard-live-grid mt-3">{liveMarketplaceLabels.map(([key, label]) => {
              const count = liveByMarketplace[key];
              const verified = key === 'ebay' && liveVerification === 'REMOTE_VERIFIED';
              const value = verified && count != null
                ? `${count} · verified`
                : key === 'ebay' && liveVerification === 'STALE_REMOTE_SNAPSHOT' && count != null
                  ? `${count} · last known`
                  : Number(count) > 0 ? `${count} · last known` : 'Not verified';
              return <div key={key} className="rounded-lg border border-slate-200 bg-white px-3 py-2"><p className="pp-dashboard-meta font-semibold">{label}</p><p className="pp-dashboard-small mt-1 font-semibold text-slate-900">{metricsReady ? value : 'Not available'}</p></div>;
            })}</div>
          </section>
        ) : null}
        {customizingMetrics ? <section className="pp-dashboard-card" aria-label="Customize dashboard metrics"><div className="pp-dashboard-card__header"><h2 className="pp-dashboard-card__title">Customize metrics</h2><Button variant="secondary" size="sm" onClick={() => void saveDashboardMetricLayout(DEFAULT_DASHBOARD_METRIC_LAYOUT)}>Reset defaults</Button></div><div className="mt-3 grid gap-2 sm:grid-cols-2">{dashboardMetricLayout.order.map((id, index) => { const item = topMetrics.find((metric) => metric.id === id); if (!item) return null; return <div key={id} className="flex min-h-11 items-center justify-between gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3"><label className="flex min-h-11 items-center gap-3 text-sm font-medium text-slate-900"><input type="checkbox" className="h-5 w-5 accent-blue-700" checked={dashboardMetricLayout.visible[id] !== false} onChange={(event) => void saveDashboardMetricLayout({ ...dashboardMetricLayout, visible: { ...dashboardMetricLayout.visible, [id]: event.target.checked } })} />Show {item.label}</label><span className="flex gap-1"><Button variant="secondary" size="sm" aria-label={`Move ${item.label} earlier`} disabled={index === 0} onClick={() => reorderDashboardMetrics(id, dashboardMetricLayout.order[index - 1])}>↑</Button><Button variant="secondary" size="sm" aria-label={`Move ${item.label} later`} disabled={index === dashboardMetricLayout.order.length - 1} onClick={() => reorderDashboardMetrics(id, dashboardMetricLayout.order[index + 1])}>↓</Button></span></div>; })}</div>{user?.is_admin ? <div className="mt-3"><Button variant="tertiary" size="sm" onClick={() => setShowMetricDetails((value) => !value)} aria-expanded={showMetricDetails}>ⓘ Metric definitions</Button>{showMetricDetails ? <dl className="mt-2 grid gap-2 text-sm text-slate-700 sm:grid-cols-2"><div><dt className="font-semibold">Ready</dt><dd>Approved records that passed stored publish checks and have no active marketplace copy.</dd></div><div><dt className="font-semibold">Needs review</dt><dd>Distinct review-ready records, unsold and not already live.</dd></div><div><dt className="font-semibold">Live</dt><dd>Distinct records in the current remote eBay snapshot or another exact-identity active projection.</dd></div><div><dt className="font-semibold">Draft</dt><dd>Unfinished records not ready for review, live, sold, or archived.</dd></div><p className="text-xs text-slate-600 sm:col-span-2">Server summary · {metricsUpdatedAt ? formatTime(metricsUpdatedAt) : 'not yet loaded'}.</p></dl> : null}</div> : null}</section> : null}
        <section className="pp-dashboard-modules" aria-label="Business operations">
          <article className="pp-dashboard-card"><div className="pp-dashboard-card__header"><h2 className="pp-dashboard-card__title">Sales</h2><Link href="/sales" className="pp-dashboard-card__link">View sales</Link></div><div className="pp-dashboard-stat-grid">{[['Today', salesDashboard.summary?.periods?.today], ['7 days', salesDashboard.summary?.periods?.last_7_days], ['30 days', salesDashboard.summary?.periods?.last_30_days]].map(([label, data]) => <div key={label} className="pp-dashboard-stat"><p className="pp-dashboard-meta">{label}</p><p className="pp-dashboard-number mt-1">{salesSummaryReady && data ? `$${Number(data.gross || 0).toFixed(0)}` : '—'}</p><p className="pp-dashboard-meta mt-1">{data ? `${data.sales} sales` : 'No data'}</p></div>)}</div></article>
          <article className="pp-dashboard-card"><div className="pp-dashboard-card__header"><h2 className="pp-dashboard-card__title">Work queue</h2><Link href="/jobs" className="pp-dashboard-card__link">View jobs</Link></div><div className="pp-dashboard-stat-grid">{[['Running', systemStatus.running_jobs, '/jobs/active'], ['Queued', systemStatus.queued_jobs, '/jobs/active'], ['Failed', systemStatus.failed_jobs, '/jobs/failed']].map(([label, value, href]) => <Link href={href} key={label} className="pp-dashboard-stat block hover:bg-blue-50"><p className="pp-dashboard-meta">{label}</p><p className={`pp-dashboard-number mt-1 ${label === 'Failed' && Number(value) > 0 ? 'text-red-700' : ''}`}>{metricsReady && value != null ? value : '—'}</p></Link>)}</div></article>
          <article className="pp-dashboard-card"><div className="pp-dashboard-card__header"><h2 className="pp-dashboard-card__title">Shipping</h2><Button href="https://www.ebay.com/sh/ord/?filter=status:AWAITING_SHIPMENT" external variant="tertiary" size="sm">eBay orders</Button></div>{shipping.status === 'REMOTE_VERIFIED' || shipping.status === 'PARTIAL_REMOTE_RESULT' ? <><p className="pp-dashboard-meta mt-1">eBay · {shipping.status === 'REMOTE_VERIFIED' ? 'live order check' : 'partial order check'}</p><div className="pp-dashboard-stat-grid">{[['Need to ship', shipping.counts?.need_to_ship], ['Due today', shipping.counts?.due_today], ['Overdue', shipping.counts?.overdue]].map(([label, count]) => <div className="pp-dashboard-stat" key={label}><p className="pp-dashboard-meta">{label}</p><p className={`pp-dashboard-number mt-1 ${label === 'Overdue' && Number(count) > 0 ? 'text-red-700' : ''}`}>{count ?? '—'}</p></div>)}</div></> : <div className="mt-3 flex items-center justify-between gap-2"><StatusPill status={shipping.status === 'NOT_CONFIGURED' ? 'warning' : 'danger'} label={shipping.status === 'AUTH_REQUIRED' ? 'Reconnect eBay' : shipping.status === 'NOT_CONFIGURED' ? 'Connect eBay' : 'Check eBay connection'} /><Button href="/settings/ebay" variant="secondary" size="sm">Fix</Button></div>}</article>
          <article className="pp-dashboard-card"><div className="pp-dashboard-card__header"><h2 className="pp-dashboard-card__title">Marketplace messages</h2><Button href="https://www.ebay.com/sh/mys/messages" external variant="tertiary" size="sm">eBay inbox</Button></div><div className="pp-dashboard-issue-row mt-2"><span className="pp-dashboard-small">eBay unread</span><strong className="pp-dashboard-small">{message.unread != null ? message.at_least ? `${message.at_least}+` : message.unread : message.status === 'AUTH_REQUIRED' ? 'Reconnect required' : 'Not verified'}</strong></div><div className="pp-dashboard-issue-row"><span className="pp-dashboard-small">Facebook</span><Button href="https://www.facebook.com/messages" external variant="tertiary" size="sm">Open Messenger</Button></div></article>
          <article className="pp-dashboard-card"><div className="pp-dashboard-card__header"><h2 className="pp-dashboard-card__title">Issues to review</h2><Link href="/notices" className="pp-dashboard-card__link">View notices</Link></div>{alertsAvailable && groupedAlerts.length ? groupedAlerts.map(([key, title, href]) => <Link href={href} key={key} className="pp-dashboard-issue-row hover:text-blue-800"><span className="pp-dashboard-small">{title}</span><strong className="pp-dashboard-small tabular-nums">{alertsByType[key]}</strong></Link>) : <p className="pp-dashboard-small mt-3">{alertsAvailable ? 'No current alerts.' : 'Alerts unavailable.'}</p>}{metricsReady && Number(systemStatus.catalog_ebay_reconciliation_needed) > 0 ? <Link href="/settings/ebay" className="pp-dashboard-issue-row mt-1 text-amber-800"><span className="pp-dashboard-small">eBay status checks</span><strong className="pp-dashboard-small">{systemStatus.catalog_ebay_reconciliation_needed}</strong></Link> : null}</article>
          <article className="pp-dashboard-card"><div className="pp-dashboard-card__header"><h2 className="pp-dashboard-card__title">Connections</h2><Link href="/settings?tab=marketplaces" className="pp-dashboard-card__link">Manage</Link></div><Link href="/settings/ebay" className="pp-dashboard-issue-row mt-2"><span className="pp-dashboard-small">eBay</span><span className="pp-dashboard-small font-semibold">{eBayConnection}</span></Link>{['facebook', 'mercari', 'poshmark', 'vinted', 'offerup'].map((market) => <Link href={`/settings?tab=marketplaces&marketplace=${market}`} className="pp-dashboard-issue-row" key={market}><span className="pp-dashboard-small capitalize">{market === 'facebook' ? 'Facebook Marketplace' : market}</span><span className="pp-dashboard-meta font-semibold">{marketplaceStatus(market)}</span></Link>)}<Link href="/settings/intake" className="pp-dashboard-issue-row"><span className="pp-dashboard-small">Google Photos</span><span className="pp-dashboard-meta font-semibold">{googleConnection}</span></Link><Link href="/settings" className="pp-dashboard-issue-row"><span className="pp-dashboard-small">OpenAI / AI</span><span className="pp-dashboard-meta font-semibold">{aiConnection}</span></Link></article>
        </section>
      </div>
    );
  };

  const sectionContent = { overview: renderDashboardSummary() };

  return (
    <AppShell
      active="/app"
      title="Dashboard"
      autonomousConfig={autonomousConfig}
      onToggleAutonomous={async () => {
        try {
          await updateServerSettings({ autonomous_mode: !autonomousConfig.autonomous_mode });
          await reload();
          toast.success(`Automatic publishing ${!autonomousConfig.autonomous_mode ? 'enabled' : 'paused'}.`);
        } catch (error) {
          toast.error(error.message || 'Automation setting could not be saved.');
        }
      }}
      contentWidth="dashboard"
      hideTopbarContext
    >
      <div className="min-w-0">{sectionContent.overview}</div>
    </AppShell>
  );
}

Dashboard.requireAuth = true;
