import { useEffect, useState } from 'react';

import { fetchEbayStatus, publishListing } from '../lib/api';

export function useEbayPublish() {
  const [publishing, setPublishing] = useState({});
  const [errors, setErrors] = useState({});
  const [success, setSuccess] = useState({});
  const [tracking, setTracking] = useState([]);

  useEffect(() => {
    if (!tracking.length) return undefined;

    const timer = setInterval(async () => {
      for (const listingId of tracking) {
        try {
          const statusData = await fetchEbayStatus(listingId);
          const status = statusData.status;
          if (status === 'POSTED' || status === 'FAILED') {
            setPublishing((prev) => ({ ...prev, [listingId]: false }));
            setTracking((prev) => prev.filter((id) => id !== listingId));
            if (status === 'POSTED') {
              setSuccess((prev) => ({
                ...prev,
                [listingId]: statusData.marketplace_data?.ebay_url || '',
              }));
            } else {
              setErrors((prev) => ({
                ...prev,
                [listingId]: statusData.marketplace_data?.error || 'Publishing failed',
              }));
            }
          }
        } catch (err) {
          setErrors((prev) => ({ ...prev, [listingId]: err.message }));
        }
      }
    }, 2500);

    return () => clearInterval(timer);
  }, [tracking]);

  const publish = async (listingId) => {
    setPublishing((prev) => ({ ...prev, [listingId]: true }));
    setErrors((prev) => ({ ...prev, [listingId]: '' }));
    setSuccess((prev) => ({ ...prev, [listingId]: '' }));
    try {
      const result = await publishListing(listingId);
      if (result.status === 'POSTED') {
        setPublishing((prev) => ({ ...prev, [listingId]: false }));
        setSuccess((prev) => ({ ...prev, [listingId]: result.ebay_url || '' }));
      } else {
        setTracking((prev) => [...new Set([...prev, listingId])]);
      }
    } catch (err) {
      setPublishing((prev) => ({ ...prev, [listingId]: false }));
      setErrors((prev) => ({ ...prev, [listingId]: err.message }));
    }
  };

  return { publish, publishing, errors, success };
}
