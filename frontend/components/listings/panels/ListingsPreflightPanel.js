import MetricCard from '../../ui/metric-card';
import SectionPanel from '../../ui/section-panel';

export default function ListingsPreflightPanel({ bulkPreflightSummary, selectedRowsLength }) {
  return (
    <SectionPanel
      title="Bulk marketplace preflight"
      description="Run preflight across selected drafts, read exact blockers, and queue only items that are safe to publish."
    >
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="Selected / scoped" value={bulkPreflightSummary.selectedCount} detail={selectedRowsLength ? 'Selected rows in the action bar.' : 'Visible filtered rows in the current view.'} />
        <MetricCard label="Checked" value={bulkPreflightSummary.checkedCount} detail="Rows with cached or freshly generated marketplace preflight summaries." />
        <MetricCard label="eBay ready" value={bulkPreflightSummary.ebayReadyCount} detail="Rows that can move to the eBay queue." />
        <MetricCard label="Facebook ready" value={bulkPreflightSummary.facebookReadyCount} detail="Rows that can be handed off to the Facebook assisted workflow." />
        <MetricCard label="Blocked" value={bulkPreflightSummary.blockedCount} detail="Rows with at least one marketplace blocker." />
        <MetricCard label="Warning only" value={bulkPreflightSummary.warningOnlyCount} detail="Rows that are ready but still need operator confirmation or review." />
        <MetricCard label="Ready to queue" value={bulkPreflightSummary.readyToQueueCount} detail="Rows with sufficient quality/readiness to queue once you choose live mode." />
        <MetricCard label="Most common blocker" value={bulkPreflightSummary.mostCommonBlocker || 'None'} detail={bulkPreflightSummary.mostCommonBlocker ? `${bulkPreflightSummary.mostCommonBlockerCount} occurrences in scope` : 'No blocker codes found in the current scope.'} />
      </div>
    </SectionPanel>
  );
}
