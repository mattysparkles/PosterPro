import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
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
import { useRouter } from "next/router";
import {
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

export default function JobsPage() {
  const { user } = useAuth();
  const router = useRouter();
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
    const requestedTab = typeof router.query.tab === "string" ? router.query.tab : "";
    if (requestedTab && JOB_TABS.some((tab) => tab.value === requestedTab)) {
      setActiveTab(requestedTab);
    }
  }, [router.query.tab]);

  useEffect(() => {
    const requestedJobId = typeof router.query.job === "string" ? Number(router.query.job) : null;
    if (!requestedJobId || activeJob) return;
    const requestedTab = typeof router.query.tab === "string" ? router.query.tab : activeTab;
    const list = requestedTab === "imports" ? (jobsOverview.import_jobs || []) : (jobsOverview.crosspost_jobs || []);
    const job = list.find((item) => Number(item.id) === requestedJobId);
    if (job) {
      setActiveJob({ type: requestedTab === "imports" ? "import" : "crosspost", job });
    }
  }, [activeJob, activeTab, jobsOverview, router.query.job, router.query.tab]);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const interval = setInterval(load, 10000);
    return () => clearInterval(interval);
  }, [autoRefresh]);

  const statusFilter = typeof router.query.status === "string" ? router.query.status : "";
  const shouldKeepStatus = (job) => {
    const status = String(job.status || "").toLowerCase();
    if (statusFilter === "active") return ["queued", "running"].includes(status);
    if (statusFilter === "completed") return status === "completed";
    if (statusFilter === "failed") return status === "failed";
    return true;
  };

  const importJobs = (jobsOverview.import_jobs || []).filter(shouldKeepStatus);
  const crosspostJobs = (jobsOverview.crosspost_jobs || []).filter(shouldKeepStatus);

  const summary = useMemo(() => {
    const queued = [...importJobs, ...crosspostJobs].filter((job) => ["queued", "running"].includes(String(job.status).toLowerCase())).length;
    const failed = [...importJobs, ...crosspostJobs].filter((job) => String(job.status).toLowerCase() === "failed").length;
    const completed = [...importJobs, ...crosspostJobs].filter((job) => String(job.status).toLowerCase() === "completed").length;
    return { queued, failed, completed };
  }, [crosspostJobs, importJobs]);

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
    { key: "status", label: "Status", render: (row) => <StatusPill status={row.status} label={row.status} /> },
    { key: "mode", label: "Requested mode", render: (row) => row.requested_mode || "auto" },
    { key: "updated", label: "Updated", render: (row) => formatTime(row.updated_at || row.created_at) },
    {
      key: "actions",
      label: "Actions",
      render: (row) => (
        <div className="flex flex-wrap gap-2">
          {["queued", "running"].includes(String(row.status).toLowerCase()) ? (
            <Button variant="outline" size="sm" onClick={() => cancelCrosspost(row.id)} disabled={!!canceling[`crosspost-${row.id}`]}>
              {canceling[`crosspost-${row.id}`] ? "Canceling..." : "Cancel"}
            </Button>
          ) : null}
          <Button variant="outline" size="sm" onClick={() => retryCrosspost(row.id)} disabled={!!retrying[`crosspost-${row.id}`]}>
            {retrying[`crosspost-${row.id}`] ? "Retrying..." : "Retry"}
          </Button>
          <Button variant="outline" size="sm" onClick={() => setActiveJob({ type: "crosspost", job: row })}>
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
    { key: "listing", label: "Created listing", render: (row) => row.created_listing_id ? <Link href={`/listings/${row.created_listing_id}`} className="font-medium text-[#2563eb]">#{row.created_listing_id}</Link> : "Pending" },
    { key: "updated", label: "Updated", render: (row) => formatTime(row.updated_at || row.created_at) },
    {
      key: "actions",
      label: "Actions",
      render: (row) => (
        <div className="flex flex-wrap gap-2">
          {["queued", "running"].includes(String(row.status).toLowerCase()) ? (
            <Button variant="outline" size="sm" onClick={() => cancelImport(row.id)} disabled={!!canceling[`import-${row.id}`]}>
              {canceling[`import-${row.id}`] ? "Canceling..." : "Cancel"}
            </Button>
          ) : null}
          <Button variant="outline" size="sm" onClick={() => retryImport(row.id)} disabled={!!retrying[`import-${row.id}`]}>
            {retrying[`import-${row.id}`] ? "Retrying..." : "Retry"}
          </Button>
          <Button variant="outline" size="sm" onClick={() => setActiveJob({ type: "import", job: row })}>
            Details
          </Button>
        </div>
      ),
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
        onChange={setActiveTab}
      />

      {activeTab === "crosspost" ? (
        <DataTable
          columns={crosspostColumns}
          rows={crosspostJobs}
          rowKey={(row) => row.id}
          onRowClick={(row) => setActiveJob({ type: "crosspost", job: row })}
          emptyState={<EmptyState title="No cross-post jobs yet" description="Queue a cross-post job from a listing workspace to start using the execution layer." className="border-0 p-0 py-6" />}
        />
      ) : (
        <DataTable
          columns={importColumns}
          rows={importJobs}
          rowKey={(row) => row.id}
          onRowClick={(row) => setActiveJob({ type: "import", job: row })}
          emptyState={<EmptyState title="No import jobs yet" description="Create a marketplace import from /listings/new to normalize an external listing into a PosterPro draft." className="border-0 p-0 py-6" />}
        />
      )}

      <Drawer
        open={!!activeJob}
        onClose={() => setActiveJob(null)}
        title={activeJob ? `${activeJob.type === "crosspost" ? "Cross-post" : "Import"} job #${activeJob.job.id}` : "Job details"}
        description="Inspect execution plans, bridge submissions, errors, and listing references without leaving the jobs console."
        widthClassName="max-w-[760px]"
      >
        {activeJob ? (
          <div className="space-y-4">
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

            {activeJob.type === "crosspost" ? (
              <div className="grid gap-4">
                <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                  <p className="text-sm font-semibold text-[#101828]">Targets</p>
                  <p className="mt-2 text-sm text-[#667085]">{(activeJob.job.target_marketplaces || []).join(", ") || "None"}</p>
                </div>
                {activeJob.job.execution_plan ? (
                  <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                    <p className="text-sm font-semibold text-[#101828]">Execution plan</p>
                    <pre className="mt-3 overflow-x-auto rounded-[10px] bg-[#f8fafc] p-3 text-xs text-[#344054]">{JSON.stringify(activeJob.job.execution_plan, null, 2)}</pre>
                  </div>
                ) : null}
                {activeJob.job.result_summary ? (
                  <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                    <p className="text-sm font-semibold text-[#101828]">Result summary</p>
                    <pre className="mt-3 overflow-x-auto rounded-[10px] bg-[#f8fafc] p-3 text-xs text-[#344054]">{JSON.stringify(activeJob.job.result_summary, null, 2)}</pre>
                  </div>
                ) : null}
              </div>
            ) : (
              <div className="grid gap-4">
                <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                  <p className="text-sm font-semibold text-[#101828]">Source marketplace</p>
                  <p className="mt-2 text-sm text-[#667085]">{activeJob.job.source_marketplace}</p>
                </div>
                {activeJob.job.normalized_preview ? (
                  <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                    <p className="text-sm font-semibold text-[#101828]">Normalized preview</p>
                    <pre className="mt-3 overflow-x-auto rounded-[10px] bg-[#f8fafc] p-3 text-xs text-[#344054]">{JSON.stringify(activeJob.job.normalized_preview, null, 2)}</pre>
                  </div>
                ) : null}
                {activeJob.job.payload ? (
                  <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                    <p className="text-sm font-semibold text-[#101828]">Source payload</p>
                    <pre className="mt-3 overflow-x-auto rounded-[10px] bg-[#f8fafc] p-3 text-xs text-[#344054]">{JSON.stringify(activeJob.job.payload, null, 2)}</pre>
                  </div>
                ) : null}
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
