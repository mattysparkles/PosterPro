import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useMemo, useState } from "react";
import toast from "react-hot-toast";

import AppShell from "../../../components/layout/AppShell";
import EmptyState from "../../../components/ui/empty-state";
import PageHeader from "../../../components/ui/page-header";
import SectionPanel from "../../../components/ui/section-panel";
import StatusPill from "../../../components/ui/status-pill";
import { useAuth } from "../../../contexts/AuthContext";
import useDashboardData from "../../../hooks/useDashboardData";
import { fetchMarketplaceImportJob, toggleAutonomousMode } from "../../../lib/api";

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
  const normalizedPreview = job.normalized_preview || {};
  const bridgeCompletion = normalizedPreview.bridge_completion || {};
  const bridgeResult = bridgeCompletion.result || {};
  const resultSummary = job.result_summary || {};

  const sections = [
    ...flattenArtifactEntries(bridgeResult.screenshots, "Bridge screenshots"),
    ...flattenArtifactEntries(resultSummary.screenshots, "Result screenshots"),
  ];

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

function RawJsonBlock({ title, value }) {
  if (!value) return null;
  return (
    <details className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
      <summary className="cursor-pointer text-sm font-semibold text-[#101828]">{title}</summary>
      <pre className="mt-3 overflow-x-auto rounded-[10px] bg-[#f8fafc] p-3 text-xs text-[#344054]">{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

export default function ImportJobDetailsPage() {
  const router = useRouter();
  const { user } = useAuth();
  const { autonomousConfig, reload } = useDashboardData(user?.id);
  const [job, setJob] = useState(null);
  const [loading, setLoading] = useState(true);

  const jobId = useMemo(() => {
    const raw = typeof router.query.jobId === "string" ? Number(router.query.jobId) : null;
    return Number.isFinite(raw) ? raw : null;
  }, [router.query.jobId]);

  useEffect(() => {
    if (!jobId) return;
    setLoading(true);
    fetchMarketplaceImportJob(jobId)
      .then((data) => setJob(data))
      .catch((error) => toast.error(error.message))
      .finally(() => setLoading(false));
  }, [jobId]);

  const artifacts = useMemo(() => extractJobArtifacts(job), [job]);
  const createdListingId = job?.created_listing_id || null;

  return (
    <AppShell
      active="/jobs"
      title={jobId ? `Import job #${jobId}` : "Import job"}
      autonomousConfig={autonomousConfig}
      onToggleAutonomous={async () => {
        await toggleAutonomousMode(!autonomousConfig.autonomous_mode);
        await reload();
      }}
      contentWidth="wide"
    >
      <PageHeader
        title={jobId ? `Import job #${jobId}` : "Import job"}
        description="Deep-linkable job detail view for operators reviewing imports, retries, and artifacts."
        actions={
          <div className="flex flex-wrap gap-2">
            <Link href="/jobs" className="text-sm font-medium text-[#2563eb]">
              Back to jobs console
            </Link>
            {createdListingId ? (
              <Link href={`/listings/${createdListingId}`} className="text-sm font-medium text-[#2563eb]">
                Open created listing #{createdListingId}
              </Link>
            ) : null}
          </div>
        }
      />

      {loading ? (
        <SectionPanel title="Loading" description="Fetching job details from the backend..." />
      ) : !job ? (
        <EmptyState title="Job not found" description="The requested import job could not be loaded." />
      ) : (
        <div className="space-y-5">
          <SectionPanel title="Status" description="Operator-facing job state and timestamps.">
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Status</p>
                <div className="mt-3">
                  <StatusPill status={job.status} label={job.status} />
                </div>
              </div>
              <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Source</p>
                <p className="mt-3 text-sm font-semibold text-[#101828]">{job.source_marketplace || "Unknown"}</p>
                <p className="mt-1 text-sm text-[#667085]">{job.source_listing_reference || "No reference recorded"}</p>
              </div>
              <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Created</p>
                <p className="mt-3 text-sm font-semibold text-[#101828]">{formatExactTime(job.created_at)}</p>
              </div>
              <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[#667085]">Updated</p>
                <p className="mt-3 text-sm font-semibold text-[#101828]">{formatExactTime(job.updated_at || job.created_at)}</p>
              </div>
            </div>
          </SectionPanel>

          <SectionPanel title="Artifacts" description="Screenshots and evidence captured during browser/provider assisted runs.">
            {artifacts.length ? (
              <div className="grid gap-3 md:grid-cols-2">
                {artifacts.map((entry) => (
                  <a
                    key={entry.asset.asset_id}
                    href={`/marketplace-jobs/bridge-assets/${entry.asset.asset_id}`}
                    className="rounded-[12px] border border-[#e5e7eb] bg-white p-4 text-sm font-medium text-[#2563eb]"
                    target="_blank"
                    rel="noreferrer"
                  >
                    {entry.label}
                  </a>
                ))}
              </div>
            ) : (
              <EmptyState title="No artifacts recorded" description="This import job did not capture screenshots or bridge assets." className="border-0 p-0 py-6" />
            )}
          </SectionPanel>

          <RawJsonBlock title="Raw normalized preview" value={job.normalized_preview} />
          <RawJsonBlock title="Raw source payload" value={job.payload} />
        </div>
      )}
    </AppShell>
  );
}

