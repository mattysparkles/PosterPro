function SummaryTile({ label, value, note, accentClassName = 'from-[#eff4ff] to-white' }) {
  return (
    <div className={`rounded-[18px] border border-white/70 bg-gradient-to-br ${accentClassName} p-4 shadow-[0_10px_24px_rgba(15,23,42,0.06)]`}>
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#667085]">{label}</p>
      <p className="mt-2 font-[var(--pp-heading-font)] text-2xl font-semibold tracking-[-0.04em] text-[#101828]">{value}</p>
      <p className="mt-2 text-sm text-[#475467]">{note}</p>
    </div>
  );
}

export default function ListingsOverviewBand({
  activeTabLabel,
  filteredCount,
  selectedCount,
  viewMode,
  listingMetrics,
  bulkPreflightSummary,
  publishJobStats,
  tabs,
  toolbar,
}) {
  return (
    <div className="space-y-5">
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.45fr)_minmax(320px,0.95fr)]">
        <div className="rounded-[22px] border border-white/70 bg-[linear-gradient(135deg,#fff7ed_0%,#ffffff_42%,#eef4ff_100%)] p-5 shadow-[0_18px_40px_rgba(15,23,42,0.08)]">
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#b54708]">Listings workspace</p>
          <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
            <div>
              <h2 className="font-[var(--pp-heading-font)] text-3xl font-semibold tracking-[-0.05em] text-[#101828]">
                {activeTabLabel}
              </h2>
              <p className="mt-2 max-w-[54ch] text-sm leading-6 text-[#475467]">
                Keep the results surface primary. Use the lower workspace modules only when you need repair, launch QA, or queue diagnostics.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <span className="rounded-full border border-white/80 bg-white/90 px-3 py-1.5 text-xs font-semibold text-[#344054]">
                {filteredCount} visible
              </span>
              <span className="rounded-full border border-white/80 bg-white/90 px-3 py-1.5 text-xs font-semibold text-[#344054]">
                {selectedCount} selected
              </span>
              <span className="rounded-full border border-white/80 bg-white/90 px-3 py-1.5 text-xs font-semibold text-[#344054]">
                {viewMode === 'table' ? 'Table layout' : 'Grid layout'}
              </span>
            </div>
          </div>
          <div className="mt-5">{tabs}</div>
        </div>

        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-2">
          <SummaryTile
            label="Review queue"
            value={listingMetrics.reviewCount}
            note={`${listingMetrics.reviewCount} listings waiting for approval`}
            accentClassName="from-[#fff1f3] to-white"
          />
          <SummaryTile
            label="Ready to publish"
            value={listingMetrics.readyCount}
            note={`$${Math.round(listingMetrics.readyAmount).toLocaleString()} in ready inventory`}
            accentClassName="from-[#ecfdf3] to-white"
          />
          <SummaryTile
            label="Blocked by preflight"
            value={bulkPreflightSummary.blockedCount}
            note={bulkPreflightSummary.mostCommonBlocker
              ? `${bulkPreflightSummary.mostCommonBlocker} is the most common blocker`
              : 'No dominant blocker detected'}
            accentClassName="from-[#fff4ed] to-white"
          />
          <SummaryTile
            label="Queue progress"
            value={`${publishJobStats.progress}%`}
            note={`${publishJobStats.completed} completed, ${publishJobStats.queued} queued/running`}
            accentClassName="from-[#eef4ff] to-white"
          />
        </div>
      </div>

      <div className="rounded-[20px] border border-white/80 bg-white/88 p-3 shadow-[0_14px_32px_rgba(15,23,42,0.06)] backdrop-blur">
        {toolbar}
      </div>
    </div>
  );
}
