export default function ListingsMarketplacePreflightBadges({
  listing,
  markets = ['ebay', 'facebook'],
  getMarketplacePreflightSummary,
  getMarketplacePreflightStatus,
  getMarketplacePreflightTone,
  getMarketplaceTopBlocker,
  getMarketplacePreflightAgeLabel,
}) {
  const primarySummary = getMarketplacePreflightSummary(listing, 'ebay') || getMarketplacePreflightSummary(listing, 'facebook');
  const blocker = getMarketplaceTopBlocker(primarySummary);

  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {markets.map((market) => {
        const summary = getMarketplacePreflightSummary(listing, market);
        const status = getMarketplacePreflightStatus(listing, market);
        const tone = getMarketplacePreflightTone(summary);
        return (
          <span
            key={`${listing.id}-${market}-preflight`}
            className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${
              tone === 'danger'
                ? 'border-[#fecdca] bg-[#fff6ed] text-[#b54708]'
                : tone === 'warning'
                ? 'border-[#fef0c7] bg-[#fffaeb] text-[#8a4b10]'
                : tone === 'success'
                ? 'border-[#abefc6] bg-[#ecfdf3] text-[#027a48]'
                : 'border-[#d0d5dd] bg-[#f9fafb] text-[#475467]'
            }`}
          >
            {market.toUpperCase()}: {status.replaceAll('_', ' ')}
          </span>
        );
      })}
      {blocker ? <span className="pp-chip">Top blocker: {blocker}</span> : null}
      {primarySummary?.last_checked_at ? (
        <span className="pp-chip">Checked {getMarketplacePreflightAgeLabel(primarySummary)}</span>
      ) : null}
    </div>
  );
}
