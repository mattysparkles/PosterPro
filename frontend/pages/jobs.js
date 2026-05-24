import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/router";
import toast from "react-hot-toast";
import { RefreshCcw } from "lucide-react";

import AppShell from "../components/layout/AppShell";
import Button from "../components/ui/button";
import DataTable from "../components/ui/data-table";
import Drawer from "../components/ui/drawer";
import EmptyState from "../components/ui/empty-state";
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
  retryCrosspostJob,
  retryMarketplaceImportJob,
  runAutomationBridgeSmokeTest,
  toggleAutonomousMode,
} from "../lib/api";

const JOB_TABS = [
  { value: "crosspost", label: "Cross-post Jobs" },
  { value: "imports", label: "Import Jobs" },
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
  const { autonomousConfig, reload: reloadDashboard } = useDashboardData(user?.id);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState("crosspost");
  const [jobsOverview, setJobsOverview] = useState({ import_jobs: [], crosspost_jobs: [] });
  const [retrying, setRetrying] = useState({});
  const [canceling, setCanceling] = useState({});
  const [activeJob, setActiveJob] = useState(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [bridgeSmoke, setBridgeSmoke] = useState(null);
  const [testingBridge, setTestingBridge] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const data = await fetchMarketplaceJobsOverview();
      setJobsOverview(data || { import_jobs: [], crosspost_jobs: [] });
    } catch (error) {
      toast.error(error.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

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

  const importJobs = jobsOverview.import_jobs || [];
  const crosspostJobs = jobsOverview.crosspost_jobs || [];

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

    const rows = queryType === "import" ? importJobs : crosspostJobs;
    const match = rows.find((row) => Number(row.id) === queryJobId);
    if (match) {
      setActiveJob((current) => {
        if (current?.type === queryType && Number(current?.job?.id) === queryJobId) {
          return current;
        }
        return { type: queryType, job: match };
      });
    }
  }, [router.isReady, router.query.type, router.query.jobId, importJobs, crosspostJobs]);

  const updateRouteState = async ({ tab, type, jobId }) => {
    const nextQuery = {};
    if (tab) nextQuery.tab = tab;
    if (type) nextQuery.type = type;
    if (jobId) nextQuery.jobId = String(jobId);
    await router.replace({ pathname: router.pathname, query: nextQuery }, undefined, { shallow: true });
  };

  const openJobDetails = async (type, job) => {
    setActiveJob({ type, job });
    await updateRouteState({ tab: type === "import" ? "imports" : "crosspost", type, jobId: job.id });
  };

  const closeJobDetails = async () => {
    setActiveJob(null);
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
    { key: "updated", label: "Updated", render: (row) => formatTime(row.updated_at || row.created_at) },
    {
      key: "actions",
      label: "Actions",
      render: (row) => (
        <div className="flex flex-wrap gap-2">
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
          <Button variant="outline" size="sm" onClick={() => void openJobDetails("crosspost", row)}>
            Details
          </Button>
        </div>
      ),
    },
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
    { key: "updated", label: "Updated", render: (row) => formatTime(row.updated_at || row.created_at) },
    {
      key: "actions",
      label: "Actions",
      render: (row) => {
        const recoverAction = ["queued", "running"].includes(String(row.status).toLowerCase()) && row.is_stale;
        return (
        <div className="flex flex-wrap gap-2">
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
          <Button variant="outline" size="sm" onClick={() => void openJobDetails("import", row)}>
            Details
          </Button>
        </div>
      )},
    },
  ];

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

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="Cross-post jobs" value={crosspostJobs.length} detail="Queued and completed outbound marketplace orchestration." />
        <MetricCard label="Import jobs" value={importJobs.length} detail="Normalized inbound marketplace imports and draft creation." />
        <MetricCard label="Queued / running" value={summary.queued} detail="Jobs still moving through workers or awaiting execution." />
        <MetricCard label="Failed" value={summary.failed} detail="Jobs that should be reviewed and potentially retried." />
      </section>

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
        </div>
      </SectionPanel>

      <Tabs
        items={[
          { value: "crosspost", label: "Cross-post Jobs", count: crosspostJobs.length },
          { value: "imports", label: "Import Jobs", count: importJobs.length },
        ]}
        value={activeTab}
        onChange={(value) => {
          setActiveTab(value);
          void updateRouteState({ tab: value, type: activeJob?.type, jobId: activeJob?.job?.id });
        }}
      />

      {activeTab === "crosspost" ? (
        <DataTable
          columns={crosspostColumns}
          rows={crosspostJobs}
          rowKey={(row) => row.id}
          onRowClick={(row) => void openJobDetails("crosspost", row)}
          emptyState={<EmptyState title="No cross-post jobs yet" description="Queue a cross-post job from a listing workspace to start using the execution layer." className="border-0 p-0 py-6" />}
        />
      ) : (
        <DataTable
          columns={importColumns}
          rows={importJobs}
          rowKey={(row) => row.id}
          onRowClick={(row) => void openJobDetails("import", row)}
          emptyState={<EmptyState title="No import jobs yet" description="Create a marketplace import from /listings/new to normalize an external listing into a PosterPro draft." className="border-0 p-0 py-6" />}
        />
      )}

      <Drawer
        open={!!activeJob}
        onClose={() => void closeJobDetails()}
        title={activeJob ? `${activeJob.type === "crosspost" ? "Cross-post" : "Import"} job #${activeJob.job.id}` : "Job details"}
        description="Inspect execution plans, bridge submissions, errors, and listing references without leaving the jobs console."
        widthClassName="max-w-[760px]"
      >
        {activeJob ? (
          <div className="space-y-4">
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
            </div>

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
