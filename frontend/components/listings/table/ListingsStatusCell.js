import StatusPill from '../../ui/status-pill';
import { isEbayReconnectRequiredError } from '../../../lib/publish-status';

export default function ListingsStatusCell({ listing, getListingBucket, errors, getListingFailureMessage }) {
  const bucket = getListingBucket(listing);
  const errorMessage = errors[listing.id];
  const failureMessage = getListingFailureMessage(listing);

  return (
    <div>
      <StatusPill status={bucket} label={bucket.charAt(0).toUpperCase() + bucket.slice(1)} />
      {errorMessage ? (
        <p className="mt-1 text-xs text-[#b42318]">
          {isEbayReconnectRequiredError(errorMessage)
            ? 'eBay token invalid. Reconnect eBay in Settings, then retry publish.'
            : errorMessage}
        </p>
      ) : failureMessage ? (
        <p className="mt-1 text-xs text-[#b42318]">{failureMessage}</p>
      ) : null}
    </div>
  );
}
