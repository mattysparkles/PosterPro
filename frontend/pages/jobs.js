import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/router";
import toast from "react-hot-toast";
import { RefreshCcw } from "lucide-react";

import AppShell from "../components/layout/AppShell";
import ActionBar from "../components/ui/action-bar";
import Button from "../components/ui/button";
import DataTable from "../components/ui/data-table";
import Drawer from "../components/ui/drawer";
import EmptyState from "../components/ui/empty-state";
import ErrorState from "../components/ui/error-state";
import HealthIndicator from "../components/ui/health-indicator";
import LoadingSkeleton from "../components/ui/loading-skeleton";
import MetricCard from "../components/ui/metric-card";
import PageHeader from "../components/ui/page-header";
import SectionPanel from "../components/ui/section-panel";
import StatusPill from "../components/ui/status-pill";
import { Tabs } from "../components/ui/tabs";
import { useAuth } from "../contexts/AuthContext";
import useDashboardData from "../hooks/useDashboardData";
import {
  buildBridgeAssetUrl,
  cancelCrosspostJob,
  cancelMarketplaceImportJob,
  fetchMarketplaceJobsOverview,
  fetchCrosspostJob,
  fetchAssistedMarketplaceJobs,
  fetchAssistedMarketplaceJob,
  fetchMarketplaceImportJob,
  fetchProcessingHealth,
  retryCrosspostJob,
  retryMarketplaceImportJob,
  bulkRequeueMarketplaceJobs,
  runAutomationBridgeSmokeTest,
  toggleAutonomousMode,
  reprioritizeCorrectionJob,
} from "../lib/api";

const JOB_TABS = [
  { value: "crosspost", label: "Cross-post Jobs" },
  { value: "assisted", label: "Assisted Marketplace Jobs" },
  { value: "imports", label: "Import Jobs" },
  { value: "corrections", label: "Correction Jobs" },
];

function formatTime(value) {
  if (!value) return "Pending";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatExactTime(value) {
  if (!value) return "Not reported";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function startCase(value) {
  return String(value || "")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase())
    .trim();
}

function jobProgress(job) {
  const total = Number(job?.target_marketplaces?.length || job?.review_items?.length || 0);
  const done = Number(job?.submitted_count || 0) + Number(job?.failed_target_count || 0) + Number(job?.review_required_count || 0);
  if (String(job?.status || '').toLowerCase() === 'completed') return 100;
  if (!total) return ['running', 'queued'].includes(String(job?.status || '').toLowerCase()) ? 15 : 0;
  return Math.min(100, Math.round((done / total) * 100));
}

function flattenArtifactEntries(value, prefix = "") {
  if (!value || typeof value !== "object") return [];
  if (Array.isArray(value)) {
    return value.flatMap((item, index) => flattenArtifactEntries(item, prefix ? `${prefix} / ${index + 1}` : `${index + 1}`));
  }
  return Object.entries(value).flatMap(([key, item]) => {
    const nextLabel = prefix ? `${prefix} / ${key}` : key;
    if (item && typeof item === "object" && item.asset_id) {
      return [{ label: nextLabel, asset: item }];
    }
    if (item && typeof item === "object" && !Array.isArray(item)) {
      return flattenArtifactEntries(item, nextLabel);
    }
    return [];
  });
}

function extractJobArtifacts(job) {
  if (!job) return [];
  const sections = [];
  const normalizedPreview = job.normalized_preview || {};
  const bridgeCompletion = normalizedPreview.bridge_completion || {};
  const bridgeResult = bridgeCompletion.result || {};
  const resultSummary = job.result_summary || {};

  sections.push(...flattenArtifactEntries(bridgeResult.screenshots, "Bridge screenshots"));
  sections.push(...flattenArtifactEntries(resultSummary.screenshots, "Result screenshots"));

  const importedListings = Array.isArray(bridgeResult.imported_listings) ? bridgeResult.imported_listings : [];
  importedListings.forEach((listing, index) => {
    sections.push(...flattenArtifactEntries(listing?.image_assets, `Imported listing ${index + 1} images`));
  });

  const deduped = [];
  const seen = new Set();
  for (const entry of sections) {
    const assetId = entry?.asset?.asset_id;
    if (!assetId || seen.has(assetId)) continue;
    seen.add(assetId);
    deduped.push(entry);
  }
  return deduped;
}

function buildCrosspostPlanEntries(job) {
  const targets = Array.isArray(job?.execution_plan?.targets) ? job.execution_plan.targets : [];
  return targets
    .filter((item) => item && typeof item === "object")
    .map((item) => ({
      marketplace: item.marketplace,
      executionMode: item.execution_mode,
      notes: Array.isArray(item.notes) ? item.notes : [],
    }));
}

function buildCrosspostExecutionEntries(job) {
  const results = Array.isArray(job?.result_summary?.results) ? job.result_summary.results : [];
  return results
    .filter((item) => item && typeof item === "object")
    .map((item) => ({
      marketplace: item.marketplace,
      status: item.status,
      executionMode: item.execution_mode,
      bridgeSubmissionStatus: item.bridge_submission?.status || item.bridge_submission?.bridge_response?.status || null,
      bridgeJobId: item.bridge_submission?.bridge_response?.job_id || null,
      bridgeCompletionStatus: item.bridge_completion?.status || item.bridge_completion?.result?.status || null,
      listingId: item.marketplace_listing_id || null,
      error: item.error || item.bridge_completion?.error || null,
    }));
}

function buildImportSummary(job) {
  const preview = job?.normalized_preview || {};
  const createdCount = Array.isArray(preview.new_listing_ids) ? preview.new_listing_ids.length : 0;
  const reusedCount = Array.isArray(preview.reused_listing_ids) ? preview.reused_listing_ids.length : 0;
  const totalCount = Array.isArray(preview.created_listing_ids) ? preview.created_listing_ids.length : createdCount + reusedCount;
  const bridgeCompletionStatus =
    preview.bridge_completion?.result?.status ||
    preview.bridge_completion?.status ||
    preview.bridge_submission?.status ||
    null;
  return {
    createdCount,
    reusedCount,
    totalCount,
    bridgeCompletionStatus,
  };
}

function RawJsonBlock({ title, value }) {
  if (!value) return null;
  return (
    <details className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
      <summary className="cursor-pointer text-sm font-semibold text-[#101828]">{title}</summary>
      <pre className="mt-3 overflow-x-auto rounded-[10px] bg-[#f8fafc] p-3 text-xs text-[#344054]">
        {JSON.stringify(value, null, 2)}
      </pre>
    </details>
  );
}

export default function JobsPage() {
  const router = useRouter();
  const { user } = useAuth();
  // Jobs only needs the autonomous toggle; avoid loading the entire catalog,
  // analytics, alerts, offers, templates, and storage datasets on navigation.
  const { autonomousConfig, reload: reloadDashboard } = useDashboardData(user?.id, {
    includeListings: false,
    includeClusters: false,
    includeMarketplaces: false,
    includeAnalytics: false,
    includeAlerts: false,
    includeOfferDashboard: false,
    includePlatformConfig: false,
    includeStorageBatches: false,
    includeListingTemplates: false,
  });
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState("crosspost");
  const [statusFilter, setStatusFilter] = useState("");
  const [jobsOverview, setJobsOverview] = useState(() => {
    if (typeof window === "undefined") return { import_jobs: [], crosspost_jobs: [] };
    try {
      return JSON.parse(window.sessionStorage.getItem("posterpro.jobs.overview") || "null") || { import_jobs: [], crosspost_jobs: [] };
    } catch {
      return { import_jobs: [], crosspost_jobs: [] };
    }
  });
  const [assistedJobs, setAssistedJobs] = useState([]);
  const [retrying, setRetrying] = useState({});
  const [canceling, setCanceling] = useState({});
  const [activeJob, setActiveJob] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [bridgeSmoke, setBridgeSmoke] = useState(null);
  const [testingBridge, setTestingBridge] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [processingHealth, setProcessingHealth] = useState(null);
  const [processingHealthError, setProcessingHealthError] = useState("");
  const [activeBlocker, setActiveBlocker] = useState(null);
  const [blockerLoading, setBlockerLoading] = useState(false);
  const loadInFlight = useRef(false);
  const defaultProcessingOrder = ["worker", "queued", "processing", "retrying", "attention", "review", "complete", "stalled", "queue_summary"];
  const [processingOrder, setProcessingOrder] = useState(defaultProcessingOrder);
  const [draggingMetric, setDraggingMetric] = useState(null);
  const overviewMetricOrder = ["crosspost", "imports", "queued", "failed"];
  const systemMetricOrder = ["catalog", "drafts", "review", "published", "sold", "intake", "queued_work", "failed_work", "notices"];
  const [overviewOrder, setOverviewOrder] = useState(overviewMetricOrder);
  const [systemOrder, setSystemOrder] = useState(systemMetricOrder);

  useEffect(() => {
    if (!user?.id) return;
    try {
      const saved = JSON.parse(window.localStorage.getItem(`posterpro.jobs.processing-order.${user.id}`) || "null");
      if (Array.isArray(saved) && saved.length === defaultProcessingOrder.length && saved.every((key) => defaultProcessingOrder.includes(key))) setProcessingOrder(saved);
      const loadOrder = (section, defaults, setter) => {
        const value = JSON.parse(window.localStorage.getItem(`posterpro.jobs.${section}-order.${user.id}`) || "null");
        if (Array.isArray(value)) { const merged = [...value.filter((key) => defaults.includes(key)), ...defaults.filter((key) => !value.includes(key))]; setter(merged); }
      };
      loadOrder("overview", overviewMetricOrder, setOverviewOrder);
      loadOrder("system", systemMetricOrder, setSystemOrder);
    } catch { /* preference cache is optional */ }
  }, [user?.id]);

  const moveMetric = (from, to) => {
    if (!from || !to || from === to) return;
    setProcessingOrder((current) => {
      const next = [...current];
      const fromIndex = next.indexOf(from); const toIndex = next.indexOf(to);
      if (fromIndex < 0 || toIndex < 0) return current;
      next.splice(fromIndex, 1); next.splice(toIndex, 0, from);
      try { if (user?.id) window.localStorage.setItem(`posterpro.jobs.processing-order.${user.id}`, JSON.stringify(next)); } catch { /* optional */ }
      return next;
    });
  };

  const moveSectionMetric = (section, from, to) => {
    if (!from || !to || from === to) return;
    const setter = section === "overview" ? setOverviewOrder : setSystemOrder;
    setter((current) => {
      const next = [...current]; const a = next.indexOf(from); const b = next.indexOf(to);
      if (a < 0 || b < 0) return current;
      next.splice(a, 1); next.splice(b, 0, from);
      try { if (user?.id) window.localStorage.setItem(`posterpro.jobs.${section}-order.${user.id}`, JSON.stringify(next)); } catch {}
      return next;
    });
  };

  const resetMetricLayout = () => {
    setOverviewOrder(overviewMetricOrder);
    setSystemOrder(systemMetricOrder);
    setProcessingOrder(defaultProcessingOrder);
    try {
      if (user?.id) {
        ["overview", "system", "processing"].forEach((section) => window.localStorage.removeItem(`posterpro.jobs.${section}-order.${user.id}`));
      }
    } catch {}
  };

  const draggableMetric = (section, key, node) => (
    <div key={key} role="group" aria-label={`Reorder ${key} metric`} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); const [fromSection, from] = String(draggingMetric || "").split(":"); if (fromSection === section) moveSectionMetric(section, from, key); setDraggingMetric(null); }} className="relative min-w-0">
      <button type="button" draggable data-drag-handle aria-label={`Drag ${key} metric`} title="Drag to reorder" onClick={(event) => event.stopPropagation()} onDragStart={(event) => { event.stopPropagation(); event.dataTransfer.effectAllowed = "move"; setDraggingMetric(`${section}:${key}`); }} onDragEnd={() => setDraggingMetric(null)} className="absolute right-3 top-3 z-10 cursor-grab rounded px-1 text-xs text-slate-400 hover:bg-slate-100 active:cursor-grabbing">⋮⋮</button>
      {node}
    </div>
  );

  const load = async () => {
    if (loadInFlight.current) return;
    loadInFlight.current = true;
    setLoading(true);
    setLoadError("");
    setProcessingHealthError("");
    try {
      const [jobsData, healthData] = await Promise.allSettled([
        fetchMarketplaceJobsOverview({ limit: 100, compact: true }),
        fetchProcessingHealth(),
      ]);
      if (jobsData.status !== "fulfilled") {
        throw jobsData.reason;
      }
      const nextOverview = jobsData.value || { import_jobs: [], crosspost_jobs: [] };
      setJobsOverview(nextOverview);
      const assistedData = await fetchAssistedMarketplaceJobs().catch(() => []);
      setAssistedJobs(Array.isArray(assistedData) ? assistedData : []);
      try { window.sessionStorage.setItem("posterpro.jobs.overview", JSON.stringify(nextOverview)); } catch { /* cache is optional */ }
      if (healthData.status === "fulfilled") {
        setProcessingHealth(healthData.value || null);
      } else {
        setProcessingHealth(null);
        setProcessingHealthError(healthData.reason?.message || "Failed to load processing health.");
      }
    } catch (error) {
      setLoadError(error.message || "Failed to load jobs overview.");
      toast.error(error.message);
    } finally {
      setLoading(false);
      loadInFlight.current = false;
    }
  };

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    if (!activeJob?.job?.id) return undefined;
    let cancelled = false;
    setDetailLoading(true);
    setDetailError("");
    const hydrate = async () => {
      try {
        const detail = activeJob.type === "crosspost"
          ? await fetchCrosspostJob(activeJob.job.id)
          : activeJob.type === "assisted"
            ? await fetchAssistedMarketplaceJob(activeJob.job.id)
            : await fetchMarketplaceImportJob(activeJob.job.id);
        if (!cancelled && detail) setActiveJob((current) => current && current.job.id === activeJob.job.id ? { ...current, job: detail } : current);
      } catch (error) {
        if (!cancelled) { setDetailError(error?.message || "The job details request failed."); toast.error(`Unable to load job details: ${error.message}`); }
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    };
    hydrate();
    return () => { cancelled = true; };
  }, [activeJob?.job?.id, activeJob?.type]);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, [autoRefresh]);

  useEffect(() => {
    if (!router.isReady) return;
    const nextTab = typeof router.query.tab === "string" ? router.query.tab : "";
    if (nextTab && JOB_TABS.some((item) => item.value === nextTab)) {
      setActiveTab(nextTab);
    }
  }, [router.isReady, router.query.tab]);

  useEffect(() => {
    setStatusFilter(typeof router.query.status === "string" ? router.query.status.toLowerCase() : "");
  }, [router.query.status]);

  const importJobs = jobsOverview.import_jobs || [];
  const crosspostJobs = jobsOverview.crosspost_jobs || [];
  const correctionJobs = jobsOverview.correction_jobs || [];
  const matchesStatus = (job) => statusFilter === "active" ? ["queued", "running"].includes(String(job.status || "").toLowerCase()) : String(job.status || "").toLowerCase() === statusFilter;
  const visibleCrosspostJobs = statusFilter ? crosspostJobs.filter(matchesStatus) : crosspostJobs;
  const visibleImportJobs = statusFilter ? importJobs.filter(matchesStatus) : importJobs;
  const visibleAssistedJobs = statusFilter
    ? assistedJobs.filter((job) => statusFilter === "active"
      ? ["queued", "claimed", "navigating", "form_filling", "awaiting_operator_review", "submitting", "submitted", "retryable"].includes(String(job.status || "").toLowerCase())
      : String(job.status || "").toLowerCase() === statusFilter)
    : assistedJobs;
  const systemStatus = jobsOverview.system_status || {};
  const metricValue = (value) => (loading && !jobsOverview.system_status ? "—" : (value ?? "—"));

  const summary = useMemo(() => {
    const queued = [...importJobs, ...crosspostJobs].filter((job) => ["queued", "running"].includes(String(job.status).toLowerCase())).length;
    const failed = [...importJobs, ...crosspostJobs].filter((job) => String(job.status).toLowerCase() === "failed").length;
    const completed = [...importJobs, ...crosspostJobs].filter((job) => String(job.status).toLowerCase() === "completed").length;
    return { queued, failed, completed };
  }, [crosspostJobs, importJobs]);

  useEffect(() => {
    if (!router.isReady) return;
    const queryType = typeof router.query.type === "string" ? router.query.type : "";
    const queryJobId = Number(router.query.jobId);
    if (!queryType || !Number.isFinite(queryJobId)) return;

    const rows = queryType === "import" ? importJobs : queryType === "assisted" ? assistedJobs : crosspostJobs;
    const match = rows.find((row) => Number(row.id) === queryJobId);
    if (match) {
      setActiveJob((current) => {
        if (current?.type === queryType && Number(current?.job?.id) === queryJobId) {
          return current;
        }
        return { type: queryType, job: match };
      });
    }
  }, [router.isReady, router.query.type, router.query.jobId, importJobs, crosspostJobs, assistedJobs]);

  const updateRouteState = async ({ tab, type, jobId }) => {
    const nextQuery = {};
    if (tab) nextQuery.tab = tab;
    if (type) nextQuery.type = type;
    if (jobId) nextQuery.jobId = String(jobId);
    await router.replace({ pathname: router.pathname, query: nextQuery }, undefined, { shallow: true });
  };

  const openJobDetails = async (type, job) => {
    setDetailError("");
    setDetailLoading(true);
    setActiveJob({ type, job });
    await updateRouteState({ tab: type === "import" ? "imports" : type === "assisted" ? "assisted" : "crosspost", type, jobId: job.id });
  };

  const closeJobDetails = async () => {
    setActiveJob(null);
    setDetailError("");
    setDetailLoading(false);
    await updateRouteState({ tab: activeTab });
  };

  const retryImport = async (jobId) => {
    setRetrying((current) => ({ ...current, [`import-${jobId}`]: true }));
    try {
      await retryMarketplaceImportJob(jobId);
      await load();
      toast.success(`Import job #${jobId} re-queued.`);
    } catch (error) {
      toast.error(error.message);
    } finally {
      setRetrying((current) => ({ ...current, [`import-${jobId}`]: false }));
    }
  };

  const retryCrosspost = async (jobId) => {
    setRetrying((current) => ({ ...current, [`crosspost-${jobId}`]: true }));
    try {
      await retryCrosspostJob(jobId);
      await load();
      toast.success(`Cross-post job #${jobId} re-queued.`);
    } catch (error) {
      toast.error(error.message);
    } finally {
      setRetrying((current) => ({ ...current, [`crosspost-${jobId}`]: false }));
    }
  };

  const cancelImport = async (jobId) => {
    setCanceling((current) => ({ ...current, [`import-${jobId}`]: true }));
    try {
      await cancelMarketplaceImportJob(jobId);
      await load();
      toast.success(`Import job #${jobId} canceled.`);
    } catch (error) {
      toast.error(error.message);
    } finally {
      setCanceling((current) => ({ ...current, [`import-${jobId}`]: false }));
    }
  };

  const cancelCrosspost = async (jobId) => {
    setCanceling((current) => ({ ...current, [`crosspost-${jobId}`]: true }));
    try {
      await cancelCrosspostJob(jobId);
      await load();
      toast.success(`Cross-post job #${jobId} canceled.`);
    } catch (error) {
      toast.error(error.message);
    } finally {
      setCanceling((current) => ({ ...current, [`crosspost-${jobId}`]: false }));
    }
  };

  const runBridgeTest = async () => {
    setTestingBridge(true);
    try {
      const result = await runAutomationBridgeSmokeTest();
      setBridgeSmoke(result);
      toast.success(result.ok ? "Automation bridge reachable." : "Automation bridge check failed.");
    } catch (error) {
      toast.error(error.message);
    } finally {
      setTestingBridge(false);
    }
  };

  const openBlocker = async (entry) => {
    await router.push(`/jobs/blockers?reason=${encodeURIComponent(entry.reason)}`);
  };

  const bulkRequeue = async (statuses) => {
    const label = statuses.join(" / ");
    if (!window.confirm(`Requeue all ${label} marketplace jobs? This will create new worker attempts for each matching job.`)) return;
    try { const result = await bulkRequeueMarketplaceJobs({ statuses }); await load(); toast.success(`${result?.count || 0} jobs requeued.`); } catch (error) { toast.error(error.message); }
  };

  const crosspostColumns = [
    { key: "id", label: "Job", render: (row) => `#${row.id}` },
    { key: "listing_id", label: "Listing", render: (row) => <Link href={`/listings/${row.listing_id}`} className="font-medium text-[#2563eb]">#{row.listing_id}</Link> },
    { key: "targets", label: "Targets", cellClassName: "min-w-[220px]", render: (row) => (row.target_marketplaces || []).join(", ") || "None" },
    {
      key: "status",
      label: "Status",
      cellClassName: "min-w-[220px]",
      render: (row) => (
        <div>
          <StatusPill status={row.status} label={row.status} />
          {row.failed_target_count ? (
            <p className="mt-1 text-xs text-[#b42318]">{row.failed_target_count} target{row.failed_target_count === 1 ? "" : "s"} failed</p>
          ) : row.review_required_count ? (
            <p className="mt-1 text-xs text-[#b54708]">{row.review_required_count} target{row.review_required_count === 1 ? "" : "s"} still need review</p>
          ) : row.submitted_count ? (
            <p className="mt-1 text-xs text-[#027a48]">{row.submitted_count} target{row.submitted_count === 1 ? "" : "s"} reached submission</p>
          ) : null}
        </div>
      ),
    },
    {
      key: "next",
      label: "Next step",
      cellClassName: "min-w-[260px]",
      render: (row) => (
        <p className="text-sm text-[#475467]">{row.operator_action || row.operator_note || "Open Details for per-target status."}</p>
      ),
    },
    { key: "mode", label: "Requested mode", render: (row) => row.requested_mode || "auto" },
    { key: "priority", label: "Priority", render: (row) => <span title="Lower number runs first" className="font-semibold">P{row.priority ?? 1}</span> },
    { key: "attempts", label: "Attempts", render: (row) => row.attempt_count ?? 0 },
    { key: "updated", label: "Updated", render: (row) => formatTime(row.updated_at || row.created_at) },
    {
      key: "actions",
      label: "Actions",
      render: (row) => (
        <div className="flex flex-wrap gap-2" onClick={(event) => event.stopPropagation()}>
          {row.can_cancel ? (
            <Button variant="outline" size="sm" onClick={() => cancelCrosspost(row.id)} disabled={!!canceling[`crosspost-${row.id}`]}>
              {canceling[`crosspost-${row.id}`] ? "Canceling..." : "Cancel"}
            </Button>
          ) : null}
          {row.can_retry ? (
            <Button variant="outline" size="sm" onClick={() => retryCrosspost(row.id)} disabled={!!retrying[`crosspost-${row.id}`]}>
              {retrying[`crosspost-${row.id}`] ? "Retrying..." : "Retry"}
            </Button>
          ) : null}
          <Button variant="outline" size="sm" data-testid={`crosspost-details-${row.id}`} onClick={(event) => { event.stopPropagation(); void openJobDetails("crosspost", row); }}>
            Details
          </Button>
        </div>
      ),
    },
  ];

  const assistedColumns = [
    { key: "id", label: "Job", render: (row) => `#${row.id}` },
    { key: "listing_id", label: "Listing", render: (row) => <Link href={`/listings/${row.listing_id}`} className="font-medium text-[#2563eb]">#{row.listing_id}</Link> },
    { key: "marketplace", label: "Marketplace", render: (row) => startCase(row.marketplace) },
    { key: "action", label: "Action", render: (row) => row.action },
    { key: "status", label: "State", render: (row) => <div><StatusPill status={row.status} label={startCase(row.status)} />{row.error_code ? <p className="mt-1 text-xs text-red-700">{row.error_code}</p> : null}</div> },
    { key: "attempt_count", label: "Attempts", render: (row) => row.attempt_count || 0 },
    { key: "device_id", label: "Device", render: (row) => row.device_id ? `#${row.device_id}` : "Unclaimed" },
  ];

  const importColumns = [
    { key: "id", label: "Job", render: (row) => `#${row.id}` },
    { key: "source_marketplace", label: "Source", render: (row) => row.source_marketplace },
    { key: "source_listing_reference", label: "Reference", cellClassName: "min-w-[220px]", render: (row) => row.source_listing_reference || "None" },
    { key: "status", label: "Status", render: (row) => <StatusPill status={row.status} label={row.status} /> },
    {
      key: "next",
      label: "Next step",
      cellClassName: "min-w-[260px]",
      render: (row) => <p className="text-sm text-[#475467]">{row.operator_action || row.operator_note || "Open Details for the current import state."}</p>,
    },
    { key: "listing", label: "Created listing", render: (row) => row.created_listing_id ? <Link href={`/listings/${row.created_listing_id}`} className="font-medium text-[#2563eb]">#{row.created_listing_id}</Link> : "Pending" },
    { key: "priority", label: "Priority", render: (row) => <span title="Lower number runs first" className="font-semibold">P{row.priority ?? 1}</span> },
    { key: "attempts", label: "Attempts", render: (row) => row.attempt_count ?? 0 },
    { key: "updated", label: "Updated", render: (row) => formatTime(row.updated_at || row.created_at) },
    {
      key: "actions",
      label: "Actions",
      render: (row) => {
        const recoverAction = ["queued", "running"].includes(String(row.status).toLowerCase()) && row.is_stale;
        return (
        <div className="flex flex-wrap gap-2" onClick={(event) => event.stopPropagation()}>
          {row.can_cancel ? (
            <Button variant="outline" size="sm" onClick={() => cancelImport(row.id)} disabled={!!canceling[`import-${row.id}`]}>
              {canceling[`import-${row.id}`] ? "Canceling..." : "Cancel"}
            </Button>
          ) : null}
          {row.can_retry ? (
            <Button variant="outline" size="sm" onClick={() => retryImport(row.id)} disabled={!!retrying[`import-${row.id}`]}>
              {retrying[`import-${row.id}`] ? (recoverAction ? "Recovering..." : "Retrying...") : (recoverAction ? "Recover" : "Retry")}
            </Button>
          ) : null}
          <Button variant="outline" size="sm" data-testid={`import-details-${row.id}`} onClick={(event) => { event.stopPropagation(); void openJobDetails("import", row); }}>
            Details
          </Button>
        </div>
      )},
    },
  ];

  const metricCard = (key, node) => draggableMetric("processing", key, node);
  const processingMetrics = {
    worker: <MetricCard label="Worker health" value={loading && !processingHealth ? "—" : (processingHealth?.worker_health?.worker_count ?? "—")} detail={processingHealth?.worker_health?.worker_count ? "Celery worker ping responded." : "Awaiting worker health response."} onClick={() => router.push("/jobs?tab=crosspost")} />,
    queued: <MetricCard label="Queued" value={loading && !processingHealth ? "—" : (processingHealth?.backlog?.queued ?? "—")} detail="Eligible backlog waiting to be resumed." onClick={() => router.push("/listings?queue=drafts")} />,
    processing: <MetricCard label="Processing" value={loading && !processingHealth ? "—" : (processingHealth?.backlog?.processing ?? "—")} detail="Rows currently leased by a worker." onClick={() => router.push("/jobs?tab=imports")} />,
    retrying: <MetricCard label="Retrying" value={loading && !processingHealth ? "—" : (processingHealth?.backlog?.retrying ?? "—")} detail="Temporary failures waiting for retry." onClick={() => router.push("/jobs?tab=crosspost")} />,
    attention: <MetricCard label="Needs attention" value={loading && !processingHealth ? "—" : (processingHealth?.backlog?.needs_attention ?? "—")} detail="Rows blocked by explicit reasons." onClick={() => router.push("/listings?queue=needs_attention")} />,
    review: <MetricCard label="Needs review" value={loading && !processingHealth ? "—" : (processingHealth?.backlog?.needs_review ?? "—")} detail="Review-ready rows waiting on operator approval." onClick={() => router.push("/listings?queue=review")} />,
    complete: <MetricCard label="Processing complete" value={loading && !processingHealth ? "—" : (processingHealth?.backlog?.complete ?? "—")} detail="Processing finished; may still also require review." onClick={() => router.push("/listings?queue=review")} />,
    stalled: <MetricCard label="Stalled" value={loading && !processingHealth ? "—" : (processingHealth?.stalled ? 1 : 0)} detail={processingHealth?.stalled ? "Processing appears stalled." : "Backlog is moving."} onClick={() => router.push("/jobs?tab=imports")} />,
    queue_summary: (
      <div
        className="pp-card pp-metric-card min-h-[146px] cursor-pointer p-5 transition hover:-translate-y-0.5 hover:shadow-md focus:outline-none focus:ring-2 focus:ring-[var(--pp-accent)]"
        role="button"
        tabIndex={0}
        onClick={() => router.push("/jobs?tab=imports")}
        onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); router.push("/jobs?tab=imports"); } }}
      >
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--pp-muted)]">Queue timing &amp; sources</p>
        <div className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
          <div><p className="text-[11px] uppercase tracking-wide text-[var(--pp-muted)]">Oldest queued</p><p className="font-semibold text-[var(--pp-text)]">{loading && !processingHealth ? "—" : (processingHealth?.backlog?.oldest_queued || "—")}</p></div>
          <div><p className="text-[11px] uppercase tracking-wide text-[var(--pp-muted)]">Last complete</p><p className="font-semibold text-[var(--pp-text)]">{loading && !processingHealth ? "—" : formatExactTime(processingHealth?.backlog?.last_success_at)}</p></div>
          <div><p className="text-[11px] uppercase tracking-wide text-[var(--pp-muted)]">Amazon Vine</p><p className="font-semibold text-[var(--pp-text)]">{loading && !processingHealth ? "—" : `${processingHealth?.backlog?.source_breakdown?.amazon_vine ?? 0} rows`}</p></div>
          <div><p className="text-[11px] uppercase tracking-wide text-[var(--pp-muted)]">Recovered</p><p className="font-semibold text-[var(--pp-text)]">{loading && !processingHealth ? "—" : `${processingHealth?.backlog?.source_breakdown?.media_inventory_recovery ?? 0} rows`}</p></div>
        </div>
      </div>
    ),
  };
  const overviewMetrics = {
    crosspost: <MetricCard onClick={() => router.push("/jobs?tab=crosspost")} label="Cross-post jobs" value={loading && !crosspostJobs.length ? "—" : crosspostJobs.length} detail="Queued and completed outbound marketplace orchestration." />,
    imports: <MetricCard onClick={() => router.push("/jobs?tab=imports")} label="Import jobs" value={loading && !importJobs.length ? "—" : importJobs.length} detail="Normalized inbound marketplace imports and draft creation." />,
    queued: <MetricCard onClick={() => router.push("/jobs?tab=crosspost&status=active")} label="Queued / running cross-post" value={loading && !jobsOverview.system_status ? "—" : crosspostJobs.filter((job) => ["queued", "running"].includes(String(job.status).toLowerCase())).length} detail="Cross-post jobs waiting or executing." />,
    failed: <MetricCard onClick={() => router.push("/jobs?tab=crosspost&status=failed")} label="Failed cross-post" value={loading && !jobsOverview.system_status ? "—" : crosspostJobs.filter((job) => String(job.status).toLowerCase() === "failed").length} detail="Cross-post jobs that need review or retry." />,
  };
  const systemMetrics = {
    catalog: <MetricCard onClick={() => router.push("/listings?queue=all")} label="Visible catalog" value={metricValue(systemStatus.catalog_visible)} detail={`${metricValue(systemStatus.catalog_total)} total listings in the catalog.`} />,
    drafts: <MetricCard onClick={() => router.push("/listings?queue=drafts")} label="Draft backlog" value={metricValue(systemStatus.catalog_drafts)} detail="Listings still being refined automatically." />,
    review: <MetricCard onClick={() => router.push("/listings?queue=review")} label="Needs review" value={metricValue(systemStatus.catalog_review)} detail="Review-ready drafts awaiting approval." />,
    published: <MetricCard onClick={() => router.push("/listings?queue=published")} label="Published" value={metricValue(systemStatus.catalog_published)} detail="Listings currently live and not sold." />,
    sold: <MetricCard onClick={() => router.push("/listings?queue=sold")} label="Sold" value={metricValue(systemStatus.catalog_sold)} detail="Listings sold and no longer active." />,
    intake: <MetricCard onClick={() => router.push("/intake/queue")} label="Intake active" value={metricValue((systemStatus.intake_batches_active ?? 0) + (systemStatus.intake_photos_processing ?? 0))} detail="Batches and photos still moving through intake." />,
    queued_work: <MetricCard onClick={() => router.push("/jobs?status=active")} label="Queued / running work" value={metricValue((systemStatus.queued_jobs ?? 0) + (systemStatus.running_jobs ?? 0))} detail="Worker tasks currently waiting or executing." />,
    failed_work: <MetricCard onClick={() => router.push("/jobs?tab=crosspost&status=failed")} label="Failed cross-post work" value={metricValue(crosspostJobs.filter((job) => String(job.status || '').toLowerCase() === 'failed').length)} detail="Cross-post jobs that need attention or retry." />,
    notices: <MetricCard onClick={() => router.push("/notifications")} label="Unread notices" value={metricValue(systemStatus.unread_notifications)} detail="Process updates waiting for review." />,
  };

  return (
    <AppShell
      active="/jobs"
      title="Jobs Console"
      autonomousConfig={autonomousConfig}
      onToggleAutonomous={async () => {
        await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
        await reloadDashboard();
      }}
      contentWidth="wide"
    >
      <PageHeader
        title="Jobs Console"
        description="Monitor import and cross-post execution across direct API, provider-assist, browser-assist, and manual handoff paths."
        actions={
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={resetMetricLayout}>Reset Layout</Button>
            <Button variant="outline" onClick={runBridgeTest} disabled={testingBridge}>
              {testingBridge ? "Testing bridge..." : "Test bridge"}
            </Button>
            <Button variant="outline" onClick={load} disabled={loading}>
              <RefreshCcw size={16} />
              Refresh
            </Button>
          </div>
        }
      />

      <section className="pp-jobs-metric-grid">{overviewOrder.map((key) => draggableMetric("overview", key, overviewMetrics[key]))}</section>
      <SectionPanel title="Live system status" description={systemStatus.status_message || "A consolidated snapshot of catalog, intake, and queue activity."}>
        <div className="pp-jobs-metric-grid">{systemOrder.map((key) => draggableMetric("system", key, systemMetrics[key]))}</div>
      </SectionPanel>

      <SectionPanel title="Processing health" description="Live backlog drain for Amazon Vine and recovered inventory. Processing state and review state are separate; complete can still also need review.">
        {processingHealthError ? <p className="mb-3 text-sm text-[#b42318]">{processingHealthError}</p> : null}
        <div className="pp-jobs-metric-grid">
          {processingOrder.map((key) => metricCard(key, processingMetrics[key]))}
        </div>
        <div className="mt-4 rounded-[12px] border border-[#e5e7eb] bg-white p-4">
          <p className="text-sm font-semibold text-[#101828]">Top blocker reasons</p>
          <p className="mt-1 text-xs text-[#667085]">
            These are the explicit reasons keeping rows out of automatic review. Items with warnings can still be reviewable; hard blockers remain here until the underlying evidence is repaired.
          </p>
          <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {(processingHealth?.backlog?.blocker_breakdown || []).slice(0, 6).map((entry) => (
              <button type="button" key={entry.reason} onClick={() => openBlocker(entry)} className="rounded-[10px] border border-[#eaecf0] bg-[#f8fafc] p-3 text-left transition hover:border-[var(--pp-accent)] hover:shadow-sm">
                <p className="text-sm font-medium text-[#101828]">{entry.reason}</p>
                <p className="mt-1 text-xs text-[#667085]">{entry.count} listings · Click to inspect and repair</p>
              </button>
            ))}
          </div>
          {activeBlocker ? (
            <div className="mt-4 rounded-[12px] border border-[var(--pp-accent)]/30 bg-white p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div><p className="text-sm font-semibold text-[#101828]">{activeBlocker.reason}</p><p className="text-xs text-[#667085]">Affected listings and evidence</p></div>
                <div className="flex gap-2"><Button size="sm" variant="outline" onClick={() => router.push(`/listings?queue=needs_attention&blocker=${encodeURIComponent(activeBlocker.reason)}`)}>Open full queue</Button><Button size="sm" variant="outline" onClick={() => setActiveBlocker(null)}>Close</Button></div>
              </div>
              {blockerLoading ? <p className="mt-3 text-sm text-[#667085]">Loading affected listings…</p> : (
                <div className="mt-3 space-y-2">
                  {(activeBlocker.items || []).map((item) => (
                    <div key={item.id} className="flex flex-wrap items-center justify-between gap-3 rounded-[10px] border border-[#eaecf0] p-3">
                      <div className="min-w-0"><Link className="text-sm font-semibold text-[var(--pp-accent)] hover:underline" href={`/listings/${item.id}`}>{item.title || `Listing #${item.id}`}</Link><p className="mt-1 text-xs text-[#667085]">#{item.id} · {item.source_type || "unknown source"} · {item.stage || item.processing_state || "blocked"}</p><p className="mt-1 text-xs text-[#475467]">{item.next_action}</p></div>
                      <div className="flex gap-2"><Button size="sm" variant="outline" onClick={() => router.push(`/listings/${item.id}`)}>Fix manually</Button><Button size="sm" onClick={() => router.push(`/listings/${item.id}?mode=repair`)}>Open AI repair</Button></div>
                    </div>
                  ))}
                  {!activeBlocker.items?.length && !blockerLoading ? <p className="text-sm text-[#667085]">No rows currently match this reason.</p> : null}
                </div>
              )}
            </div>
          ) : null}
        </div>
      </SectionPanel>
      <ActionBar
        left={<HealthIndicator healthy={!bridgeSmoke || bridgeSmoke.ok} label={bridgeSmoke?.ok ? "Bridge healthy" : bridgeSmoke ? "Bridge needs attention" : "Bridge not tested"} />}
        right={<span>{autoRefresh ? "Auto-refresh on" : "Auto-refresh off"}</span>}
      />

      <SectionPanel title="Execution model" description="The same console tracks live eBay API work and structured secondary-marketplace handoff jobs.">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {[
            ["direct_api", "Live publish path for supported channels like eBay."],
            ["provider_assist", "Build a provider packet for a unified marketplace service."],
            ["browser_assist", "Build a browser automation handoff with shipping and renewal context."],
            ["manual_only", "Keep a structured operator packet when no direct automation is available."],
          ].map(([label, detail]) => (
            <div key={label} className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
              <p className="text-sm font-semibold text-[#101828]">{label}</p>
              <p className="mt-1 text-sm text-[#667085]">{detail}</p>
            </div>
          ))}
        </div>
      </SectionPanel>

      <SectionPanel title="Operator controls" description="Use auto-refresh, bridge checks, and job detail inspection from the same console.">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          <label className="flex items-center justify-between rounded-[12px] border border-[#e5e7eb] bg-white p-4 text-sm font-medium text-[#101828]">
            Auto-refresh every 10s
            <input type="checkbox" checked={autoRefresh} onChange={(event) => setAutoRefresh(event.target.checked)} />
          </label>
          <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
            <p className="text-sm font-semibold text-[#101828]">Bridge smoke test</p>
            <p className="mt-1 text-sm text-[#667085]">
              {bridgeSmoke ? (bridgeSmoke.ok ? "Bridge reachable from the app server." : bridgeSmoke.message || "Bridge check failed.") : "Run a connectivity test for provider/browser-assisted marketplaces."}
            </p>
          </div>
          <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
            <p className="text-sm font-semibold text-[#101828]">Live status</p>
            <p className="mt-1 text-sm text-[#667085]">Queued and running jobs will update automatically while auto-refresh stays enabled.</p>
          </div>
          <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
            <p className="text-sm font-semibold text-[#101828]">Bulk operations</p>
            <p className="mt-1 text-xs text-[#667085]">Requeue actions require confirmation and create independent worker attempts.</p>
            <div className="mt-3 flex flex-wrap gap-2"><Button size="sm" variant="outline" onClick={() => void bulkRequeue(["failed"])}>Requeue failed</Button><Button size="sm" variant="outline" onClick={() => void bulkRequeue(["queued"])}>Requeue queued</Button><Button size="sm" variant="outline" onClick={() => void bulkRequeue(["completed"])}>Re-run completed</Button></div>
          </div>
        </div>
      </SectionPanel>

      <Tabs
        items={[
          { value: "crosspost", label: "Cross-post Jobs", count: crosspostJobs.length },
          { value: "assisted", label: "Assisted Marketplace Jobs", count: assistedJobs.length },
          { value: "imports", label: "Import Jobs", count: importJobs.length },
          { value: "corrections", label: "Correction Jobs", count: correctionJobs.length },
        ]}
        value={activeTab}
        onChange={(value) => {
          setActiveTab(value);
          void updateRouteState({ tab: value, type: activeJob?.type, jobId: activeJob?.job?.id });
        }}
      />
      {loadError ? <ErrorState title="Jobs feed unavailable" description={loadError} action={<Button variant="outline" onClick={load}>Retry</Button>} /> : null}
      {loading ? <LoadingSkeleton lines={6} className="mb-4" /> : null}

      {activeTab === "corrections" ? (
        <SectionPanel title="Manual correction jobs" description="Prioritized operator correction work and material results."><div className="space-y-2">{correctionJobs.map((job) => <article key={job.id} className="rounded border p-3 text-sm"><div className="flex flex-wrap justify-between gap-2"><b>Correction #{job.id} · Listing #{job.listing_id}</b><StatusPill status={job.status} label={`${String(job.status || '').toUpperCase()} · P${job.priority}`} /></div><p className="mt-1 text-xs text-slate-600">Requested: {(job.fields || []).join(', ') || 'none'} · Attempts: {job.attempt_count || 0} · By: {job.requested_by || 'operator'} · {job.created_at ? new Date(job.created_at).toLocaleString() : ''}</p>{job.status === 'queued' ? <label className="mt-1 block text-xs">Priority <input type="number" min="0" value={job.priority} onChange={async (e) => { try { await reprioritizeCorrectionJob(job.id, Number(e.target.value)); await load(); } catch (err) { toast.error(err.message); } }} className="ml-1 w-16 rounded border px-1" /></label> : null}{job.operator_note ? <p className="mt-1 rounded bg-blue-50 p-2 text-xs"><b>Operator instruction:</b> {job.operator_note}</p> : null}{job.failure_reason ? <p className="mt-1 text-xs text-red-700">Blocker: {job.failure_reason}</p> : null}<details className="mt-2 text-xs"><summary className="cursor-pointer font-semibold">Correction details</summary><div className="mt-2 grid gap-2 md:grid-cols-2"><div><b>Before</b><pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2">{JSON.stringify(job.before || {},null,2)}</pre></div><div><b>After</b><pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2">{JSON.stringify(job.after || {},null,2)}</pre></div></div><div className="mt-2"><b>Per-field validation</b>{(job.result?.field_results || []).map((field) => <div key={field.field} className="mt-1 rounded border p-2"><b>{field.field}</b>: {field.decision} · {field.material_change ? 'material change' : 'no material change'}<br/><span className="text-slate-600">Capability: {field.capability_used || '—'} · Evidence: {field.evidence_used ? 'yes' : 'no'} · Validation: {field.validation_after || '—'}</span></div>)}</div><details className="mt-2"><summary>Raw diagnostics</summary><pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap">{JSON.stringify({material_delta:job.material_delta,result:job.result},null,2)}</pre></details></details></article>)}{!correctionJobs.length ? <p className="text-sm text-slate-500">No correction jobs.</p> : null}</div></SectionPanel>
      ) : activeTab === "crosspost" ? (
        <DataTable
          columns={crosspostColumns}
          rows={visibleCrosspostJobs}
          rowKey={(row) => row.id}
          onRowClick={(row) => void openJobDetails("crosspost", row)}
          emptyState={<EmptyState title="No cross-post jobs yet" description="Queue a cross-post job from a listing workspace to start using the execution layer." className="border-0 p-0 py-6" />}
        />
      ) : activeTab === "assisted" ? (
        <DataTable
          columns={assistedColumns}
          rows={visibleAssistedJobs}
          rowKey={(row) => row.id}
          onRowClick={(row) => void openJobDetails("assisted", row)}
          emptyState={<EmptyState title="No assisted marketplace jobs" description="Assisted create/end actions appear here after PosterPro queues them for a paired browser extension." className="border-0 p-0 py-6" />}
        />
      ) : (
        <DataTable
          columns={importColumns}
          rows={visibleImportJobs}
          rowKey={(row) => row.id}
          onRowClick={(row) => void openJobDetails("import", row)}
          emptyState={<EmptyState title="No import jobs yet" description="Create a marketplace import from /listings/new to normalize an external listing into a PosterPro draft." className="border-0 p-0 py-6" />}
        />
      )}

      <Drawer
        open={!!activeJob}
        onClose={() => void closeJobDetails()}
        title={activeJob ? `${activeJob.type === "crosspost" ? "Cross-post" : activeJob.type === "assisted" ? "Assisted marketplace" : "Import"} job #${activeJob.job.id}` : "Job details"}
        description="Inspect execution plans, bridge submissions, errors, and listing references without leaving the jobs console."
        widthClassName="max-w-[760px]"
      >
        {activeJob ? (
          <div className="space-y-4">
            {detailLoading ? <div role="status" className="rounded-[12px] border border-blue-200 bg-blue-50 p-4 text-sm text-blue-800">Loading job details…</div> : null}
            {detailError ? <div role="alert" className="rounded-[12px] border border-red-200 bg-red-50 p-4 text-sm text-red-800"><p className="font-semibold">Unable to load job details</p><p className="mt-1">{detailError}</p></div> : null}
            {(() => {
              const artifacts = extractJobArtifacts(activeJob.job);
              return artifacts.length ? (
                <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                  <p className="text-sm font-semibold text-[#101828]">Artifacts</p>
                  <div className="mt-3 grid gap-3 md:grid-cols-2">
                    {artifacts.map((entry) => (
                      <a
                        key={entry.asset.asset_id}
                        href={buildBridgeAssetUrl(entry.asset.asset_id)}
                        target="_blank"
                        rel="noreferrer"
                        className="overflow-hidden rounded-[12px] border border-[#e5e7eb] bg-[#f8fafc] transition hover:border-[#bfd2ff] hover:bg-white"
                      >
                        <img
                          src={buildBridgeAssetUrl(entry.asset.asset_id)}
                          alt={entry.label}
                          className="h-40 w-full object-cover"
                        />
                        <div className="p-3">
                          <p className="text-sm font-medium text-[#101828]">{entry.label}</p>
                          <p className="mt-1 text-xs text-[#667085]">{entry.asset.file_name || entry.asset.asset_id}</p>
                        </div>
                      </a>
                    ))}
                  </div>
                </div>
              ) : null;
            })()}
            <div className="grid gap-3 md:grid-cols-2">
              <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Status</p>
                <div className="mt-2">
                  <StatusPill status={activeJob.job.status} label={activeJob.job.status} />
                </div>
              </div>
              <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Updated</p>
                <p className="mt-2 text-sm font-semibold text-[#101828]">{formatTime(activeJob.job.updated_at || activeJob.job.created_at)}</p>
              </div>
              <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Requested by</p>
                <p className="mt-2 text-sm font-semibold text-[#101828]">{activeJob.job.operator_email || `User #${activeJob.job.user_id || "—"}`}</p>
              </div>
            </div>

            <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
              <p className="text-sm font-semibold text-[#101828]">Audit timeline</p>
              <div className="mt-3 space-y-2 border-l-2 border-[#d0d5dd] pl-4">
                <div><p className="text-sm font-medium text-[#101828]">Job created / requested</p><p className="text-xs text-[#667085]">{formatExactTime(activeJob.job.created_at)} · {activeJob.job.operator_email || `User #${activeJob.job.user_id || "—"}`}</p></div>
                <div><p className="text-sm font-medium text-[#101828]">Current worker status: {startCase(activeJob.job.status)}</p><p className="text-xs text-[#667085]">Last update {formatExactTime(activeJob.job.updated_at || activeJob.job.created_at)} · task {activeJob.job.task_id || "not recorded"}</p></div>
                {activeJob.job.last_error ? <div><p className="text-sm font-medium text-[#b42318]">Failure / blocker</p><p className="text-xs text-[#912018]">{activeJob.job.last_error}</p></div> : null}
                {activeJob.job.operator_note ? <div><p className="text-sm font-medium text-[#101828]">Operator/system result</p><p className="text-xs text-[#475467]">{activeJob.job.operator_note}</p></div> : null}
              </div>
            </div>

            <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-semibold text-[#101828]">Progress</p>
                <p className="text-sm font-semibold text-[#475467]">{jobProgress(activeJob.job)}%</p>
              </div>
              <div className="mt-3 h-2 overflow-hidden rounded-full bg-[#eaecf0]">
                <div className="h-full rounded-full bg-gradient-to-r from-[#173a63] to-[#c9a160] transition-all" style={{ width: `${jobProgress(activeJob.job)}%` }} />
              </div>
            </div>

            {activeJob.type === "crosspost" && activeJob.job.listing_id ? (
              <div className="rounded-[12px] border border-[#c7d7fe] bg-[#eff4ff] p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Originating listing</p>
                <Link href={`/listings/${activeJob.job.listing_id}`} className="mt-2 inline-flex font-semibold text-[#175cd3] hover:underline">
                  Open listing #{activeJob.job.listing_id} →
                </Link>
              </div>
            ) : null}
            {activeJob.type === "assisted" ? (
              <div className="rounded-[12px] border border-[#c7d7fe] bg-[#eff4ff] p-4">
                <p className="text-sm font-semibold text-[#101828]">{startCase(activeJob.job.marketplace)} · {activeJob.job.action}</p>
                <p className="mt-2 text-sm text-[#344054]">State: {startCase(activeJob.job.status)} · Attempts: {activeJob.job.attempt_count || 0} · Device: {activeJob.job.device_id ? `#${activeJob.job.device_id}` : "not claimed"}</p>
                <p className="mt-1 text-xs text-[#667085]">Claimed {formatExactTime(activeJob.job.claimed_at)} · Started {formatExactTime(activeJob.job.started_at)} · Completed {formatExactTime(activeJob.job.completed_at)}</p>
                {activeJob.job.external_listing_id ? <p className="mt-2 break-all text-sm">External ID: {activeJob.job.external_listing_id}</p> : null}
                {activeJob.job.external_url ? <a href={activeJob.job.external_url} target="_blank" rel="noreferrer" className="mt-1 inline-block break-all text-sm text-[#175cd3] hover:underline">Open external listing</a> : null}
                {activeJob.job.error_code || activeJob.job.error_detail ? <p role="alert" className="mt-2 rounded bg-red-50 p-2 text-sm text-[#912018]">{activeJob.job.error_code ? `${activeJob.job.error_code}: ` : ""}{activeJob.job.error_detail || "No additional failure detail."}</p> : null}
                <p className="mt-3 text-xs font-medium text-[#475467]">Payload snapshot</p>
                <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap rounded bg-white p-3 text-xs">{JSON.stringify(activeJob.job.payload || {}, null, 2)}</pre>
              </div>
            ) : null}
            {activeJob.type === "import" && activeJob.job.created_listing_id ? (
              <div className="rounded-[12px] border border-[#c7d7fe] bg-[#eff4ff] p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Created listing</p>
                <Link href={`/listings/${activeJob.job.created_listing_id}`} className="mt-2 inline-flex font-semibold text-[#175cd3] hover:underline">
                  Open listing #{activeJob.job.created_listing_id} →
                </Link>
              </div>
            ) : null}

            <div className="grid gap-3 md:grid-cols-3">
              <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Created</p>
                <p className="mt-2 text-sm font-semibold text-[#101828]">{formatExactTime(activeJob.job.created_at)}</p>
              </div>
              <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Last worker update</p>
                <p className="mt-2 text-sm font-semibold text-[#101828]">{formatExactTime(activeJob.job.updated_at || activeJob.job.created_at)}</p>
              </div>
              <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Task id</p>
                <p className="mt-2 break-all text-sm font-semibold text-[#101828]">{activeJob.job.task_id || "Not recorded"}</p>
              </div>
            </div>

            {activeJob.job.operator_action ? (
              <div className="rounded-[12px] border border-[#c7d7fe] bg-[#eff4ff] p-4">
                <p className="text-sm font-semibold text-[#101828]">Next step</p>
                <p className="mt-2 text-sm text-[#344054]">{activeJob.job.operator_action}</p>
              </div>
            ) : null}

            {activeJob.job.operator_note ? (
              <div className="rounded-[12px] border border-[#d0d5dd] bg-[#f8fafc] p-4">
                <p className="text-sm font-semibold text-[#101828]">Operator note</p>
                <p className="mt-2 text-sm text-[#475467]">{activeJob.job.operator_note}</p>
              </div>
            ) : null}

            {activeJob.type === "crosspost" ? (
              <div className="grid gap-4">
                <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                  <p className="text-sm font-semibold text-[#101828]">Targets</p>
                  <p className="mt-2 text-sm text-[#667085]">{(activeJob.job.target_marketplaces || []).join(", ") || "None"}</p>
                </div>
                {(activeJob.job.assisted_jobs || []).length ? (
                  <div className="rounded-[12px] border border-[#c7d7fe] bg-[#f8faff] p-4">
                    <p className="text-sm font-semibold text-[#101828]">Browser-assisted marketplace jobs</p>
                    <div className="mt-3 space-y-3">
                      {activeJob.job.assisted_jobs.map((job) => (
                        <div key={job.id} className="rounded-[10px] border border-[#dbe7ff] bg-white p-3">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="text-sm font-semibold text-[#101828]">{startCase(job.marketplace)} · {job.action} · job #{job.id}</p>
                            <StatusPill status={job.status} label={startCase(job.status)} />
                          </div>
                          <p className="mt-1 text-xs text-[#667085]">Device #{job.device_id || "not claimed"} · attempts {job.attempt_count || 0} · claimed {formatExactTime(job.claimed_at)} · finished {formatExactTime(job.completed_at)}</p>
                          {job.external_listing_id ? <p className="mt-1 break-all text-xs text-[#344054]">External listing: {job.external_listing_id}</p> : null}
                          {job.external_url ? <a className="mt-1 inline-block break-all text-xs text-[#175cd3] hover:underline" href={job.external_url} target="_blank" rel="noreferrer">Open marketplace listing</a> : null}
                          {job.error_code || job.error_detail ? <p role="alert" className="mt-2 rounded-md bg-red-50 p-2 text-xs text-[#912018]">{job.error_code ? `${job.error_code}: ` : ""}{job.error_detail || "Marketplace action failed."}</p> : null}
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
                <div className="grid gap-3 md:grid-cols-3">
                  <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                    <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Submitted</p>
                    <p className="mt-2 text-sm font-semibold text-[#101828]">{activeJob.job.submitted_count || 0}</p>
                  </div>
                  <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                    <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Needs review</p>
                    <p className="mt-2 text-sm font-semibold text-[#101828]">{activeJob.job.review_required_count || 0}</p>
                  </div>
                  <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                    <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Failed targets</p>
                    <p className="mt-2 text-sm font-semibold text-[#101828]">{activeJob.job.failed_target_count || 0}</p>
                  </div>
                </div>
                {buildCrosspostPlanEntries(activeJob.job).length ? (
                  <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                    <p className="text-sm font-semibold text-[#101828]">Planned execution</p>
                    <div className="mt-3 space-y-3">
                      {buildCrosspostPlanEntries(activeJob.job).map((target) => (
                        <div key={`plan-${target.marketplace}-${target.executionMode}`} className="rounded-[10px] border border-[#eaecf0] bg-[#f8fafc] p-3">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="text-sm font-semibold text-[#101828]">{startCase(target.marketplace)}</p>
                            {target.executionMode ? <StatusPill status="info" label={startCase(target.executionMode)} /> : null}
                          </div>
                          {target.notes.length ? (
                            <div className="mt-2 space-y-1">
                              {target.notes.map((note) => (
                                <p key={note} className="text-sm text-[#475467]">{note}</p>
                              ))}
                            </div>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
                {Array.isArray(activeJob.job.target_outcomes) && activeJob.job.target_outcomes.length ? (
                  <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                    <p className="text-sm font-semibold text-[#101828]">Target outcomes</p>
                    <div className="mt-3 space-y-3">
                      {activeJob.job.target_outcomes.map((target) => (
                        <div key={`${target.marketplace}-${target.execution_mode}`} className="rounded-[10px] border border-[#eaecf0] bg-[#f8fafc] p-3">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="text-sm font-semibold text-[#101828]">{startCase(target.marketplace)}</p>
                            {target.result_status ? <StatusPill status={target.result_status} label={startCase(target.result_status)} /> : null}
                          </div>
                          <p className="mt-1 text-xs text-[#667085]">{startCase(target.execution_mode || "unknown mode")}</p>
                          {target.operator_note ? <p className="mt-2 text-sm text-[#475467]">{target.operator_note}</p> : null}
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
                {buildCrosspostExecutionEntries(activeJob.job).length ? (
                  <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                    <p className="text-sm font-semibold text-[#101828]">Bridge execution detail</p>
                    <div className="mt-3 space-y-3">
                      {buildCrosspostExecutionEntries(activeJob.job).map((entry) => (
                        <div key={`execution-${entry.marketplace}-${entry.bridgeJobId || entry.status || "result"}`} className="rounded-[10px] border border-[#eaecf0] bg-[#f8fafc] p-3">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="text-sm font-semibold text-[#101828]">{startCase(entry.marketplace)}</p>
                            {entry.status ? <StatusPill status={entry.status} label={startCase(entry.status)} /> : null}
                            {entry.executionMode ? <StatusPill status="info" label={startCase(entry.executionMode)} /> : null}
                          </div>
                          <div className="mt-2 grid gap-2 md:grid-cols-2">
                            <div className="rounded-[10px] border border-white bg-white p-3">
                              <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[#667085]">Bridge submission</p>
                              <p className="mt-1 text-sm text-[#101828]">{startCase(entry.bridgeSubmissionStatus || "Not reported")}</p>
                              {entry.bridgeJobId ? <p className="mt-1 break-all text-xs text-[#667085]">Job id: {entry.bridgeJobId}</p> : null}
                            </div>
                            <div className="rounded-[10px] border border-white bg-white p-3">
                              <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[#667085]">Bridge completion</p>
                              <p className="mt-1 text-sm text-[#101828]">{startCase(entry.bridgeCompletionStatus || "Pending")}</p>
                              {entry.listingId ? <p className="mt-1 text-xs text-[#667085]">Marketplace listing row #{entry.listingId}</p> : null}
                            </div>
                          </div>
                          {entry.error ? <p className="mt-2 text-sm text-[#912018]">{entry.error}</p> : null}
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
                <RawJsonBlock title="Raw execution plan" value={activeJob.job.execution_plan} />
                <RawJsonBlock title="Raw result summary" value={activeJob.job.result_summary} />
              </div>
            ) : (
              <div className="grid gap-4">
                <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                  <p className="text-sm font-semibold text-[#101828]">Source marketplace</p>
                  <p className="mt-2 text-sm text-[#667085]">{activeJob.job.source_marketplace}</p>
                </div>
                <div className="grid gap-3 md:grid-cols-3">
                  <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                    <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Listings resolved</p>
                    <p className="mt-2 text-sm font-semibold text-[#101828]">{buildImportSummary(activeJob.job).totalCount}</p>
                  </div>
                  <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                    <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">New drafts</p>
                    <p className="mt-2 text-sm font-semibold text-[#101828]">{buildImportSummary(activeJob.job).createdCount}</p>
                  </div>
                  <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                    <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Reused drafts</p>
                    <p className="mt-2 text-sm font-semibold text-[#101828]">{buildImportSummary(activeJob.job).reusedCount}</p>
                  </div>
                </div>
                {buildImportSummary(activeJob.job).bridgeCompletionStatus ? (
                  <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                    <p className="text-sm font-semibold text-[#101828]">Bridge import state</p>
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <StatusPill status={buildImportSummary(activeJob.job).bridgeCompletionStatus} label={startCase(buildImportSummary(activeJob.job).bridgeCompletionStatus)} />
                      {activeJob.job.source_listing_reference ? (
                        <p className="text-sm text-[#667085]">Source reference: {activeJob.job.source_listing_reference}</p>
                      ) : null}
                    </div>
                  </div>
                ) : null}
                {Array.isArray(activeJob.job.review_items) && activeJob.job.review_items.length ? (
                  <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                    <p className="text-sm font-semibold text-[#101828]">Imported listing review</p>
                    <div className="mt-3 space-y-3">
                      {activeJob.job.review_items.map((item) => (
                        <div key={item.listing_id} className="rounded-[10px] border border-[#eaecf0] bg-[#f8fafc] p-3">
                          <div className="flex flex-wrap items-center gap-2">
                            <Link href={`/listings/${item.listing_id}`} className="text-sm font-semibold text-[#2563eb]">
                              #{item.listing_id}
                            </Link>
                            {item.needs_review ? <StatusPill status="review_required" label="Review required" /> : <StatusPill status={item.status} label={startCase(item.status)} />}
                          </div>
                          <p className="mt-1 text-sm text-[#475467]">{item.title || "Untitled listing"}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
                <RawJsonBlock title="Raw normalized preview" value={activeJob.job.normalized_preview} />
                <RawJsonBlock title="Raw source payload" value={activeJob.job.payload} />
              </div>
            )}

            {activeJob.job.last_error ? (
              <div className="rounded-[12px] border border-[#fecdca] bg-[#fff6f3] p-4">
                <p className="text-sm font-semibold text-[#912018]">Last error</p>
                <p className="mt-2 text-sm text-[#912018]">{activeJob.job.last_error}</p>
              </div>
            ) : null}
          </div>
        ) : null}
      </Drawer>
    </AppShell>
  );
}

JobsPage.requireAuth = true;
