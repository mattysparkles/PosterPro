import Button from '../../ui/button';
import MetricCard from '../../ui/metric-card';
import SectionPanel from '../../ui/section-panel';

export default function ListingsLaunchCandidatesPanel({
  launchCandidatesLoading,
  refreshLaunchCandidates,
  runPreflightOnCandidates,
  runLaunchDrillForCandidates,
  openFirstCandidate,
  exportLaunchCandidatesCsv,
  ebayAccountReadiness,
  launchCandidatesReport,
}) {
  return (
    <SectionPanel
      title="Launch QA candidates"
      description="Small low-risk eBay launch set for safe production acceptance and drill testing."
      action={
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={refreshLaunchCandidates} disabled={launchCandidatesLoading}>
            {launchCandidatesLoading ? 'Refreshing…' : 'Run candidate selector'}
          </Button>
          <Button variant="outline" size="sm" onClick={runPreflightOnCandidates} disabled={launchCandidatesLoading}>
            Run preflight on candidates
          </Button>
          <Button variant="outline" size="sm" onClick={runLaunchDrillForCandidates} disabled={launchCandidatesLoading}>
            Dry-run launch drill
          </Button>
          <Button variant="outline" size="sm" onClick={openFirstCandidate} disabled={launchCandidatesLoading}>
            Open first candidate
          </Button>
          <Button variant="outline" size="sm" onClick={exportLaunchCandidatesCsv} disabled={launchCandidatesLoading}>
            Export candidate QA CSV
          </Button>
        </div>
      }
    >
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="eBay readiness" value={ebayAccountReadiness?.publish_ready ? 'Ready' : 'Needs setup'} detail={ebayAccountReadiness?.status_note || 'Account readiness unavailable.'} />
        <MetricCard label="Policies present" value={ebayAccountReadiness?.policies_present ? 'Yes' : 'No'} detail="Payment, fulfillment, and return policies." />
        <MetricCard label="Location key" value={ebayAccountReadiness?.location_present ? 'Yes' : 'No'} detail="Merchant location configured for eBay inventory." />
        <MetricCard label="Candidates" value={(launchCandidatesReport?.candidates || []).length || 0} detail="Low-risk listings selected for a controlled drill." />
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-2">
        {(launchCandidatesReport?.candidates || []).slice(0, 6).map((item) => (
          <div key={`launch-candidate-${item.listing_id}`} className="rounded-[12px] border border-[#e5e7eb] bg-[#fcfcfd] p-3">
            <p className="text-sm font-semibold text-[#101828]">{item.title || `Listing #${item.listing_id}`}</p>
            <p className="mt-1 text-xs text-[#667085]">#{item.listing_id} · ${Number(item.price || 0).toFixed(2)} · {String(item.preflight_status || 'unknown').replaceAll('_', ' ')}</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {(item.top_warnings || []).slice(0, 3).map((warning) => (
                <span key={`${item.listing_id}-${warning}`} className="pp-chip">{warning}</span>
              ))}
            </div>
            <p className="mt-2 text-xs text-[#475467]">{item.reason_selected || 'Selected for launch drill.'}</p>
          </div>
        ))}
        {!(launchCandidatesReport?.candidates || []).length ? (
          <div className="rounded-[12px] border border-dashed border-[#d0d5dd] bg-white p-4 text-sm text-[#667085]">
            Run the candidate selector to identify 5-10 low-risk eBay drafts for the launch drill.
          </div>
        ) : null}
      </div>
    </SectionPanel>
  );
}
