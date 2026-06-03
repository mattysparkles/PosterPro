import { useCallback, useEffect, useState } from 'react';

import { fetchMarketplaceStatus, publishListing, publishListingEbay } from '../lib/api';

export function useMarketplacePublish() {
  const [publishing, setPublishing] = useState({});
  const [errors, setErrors] = useState({});
  const [statusByListing, setStatusByListing] = useState({});
  const [tracking, setTracking] = useState([]);

  const refreshStatus = useCallback(async (listingId) => {
    const data = await fetchMarketplaceStatus(listingId);
    setStatusByListing((prev) => ({ ...prev, [listingId]: data.marketplaces || [] }));
    return data.marketplaces || [];
  }, []);

  useEffect(() => {
    if (!tracking.length) return undefined;
    const timer = setInterval(async () => {
      for (const listingId of tracking) {
        try {
          const statuses = await refreshStatus(listingId);
          if (statuses.every((s) => s.status !== 'PENDING')) {
            setPublishing((prev) => ({ ...prev, [listingId]: false }));
            setTracking((prev) => prev.filter((id) => id !== listingId));
          }
        } catch (err) {
          setErrors((prev) => ({ ...prev, [listingId]: err.message }));
        }
      }
    }, 3000);

    return () => clearInterval(timer);
  }, [tracking, refreshStatus]);

  const publish = async (listingId, marketplaces) => {
    setPublishing((prev) => ({ ...prev, [listingId]: true }));
    setErrors((prev) => ({ ...prev, [listingId]: '' }));
    try {
      const normalizedMarketplaces = Array.isArray(marketplaces)
        ? marketplaces.map((market) => String(market).toLowerCase()).filter(Boolean)
        : [];
      if (normalizedMarketplaces.length === 1 && normalizedMarketplaces[0] === 'ebay') {
        await publishListingEbay(listingId);
      } else {
        await publishListing(listingId, marketplaces);
      }
      setTracking((prev) => [...new Set([...prev, listingId])]);
      await refreshStatus(listingId);
    } catch (err) {
      setPublishing((prev) => ({ ...prev, [listingId]: false }));
      setErrors((prev) => ({ ...prev, [listingId]: err.message }));
    }
  };

  return { publish, publishing, errors, statusByListing, refreshStatus };
}
