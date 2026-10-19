import { useCallback, useEffect, useState } from 'react';

import { fetchStorageUnitBatch } from '../lib/api';

export function useBatchProgress() {
  const [batchStatusById, setBatchStatusById] = useState({});
  const [tracking, setTracking] = useState([]);

  const refreshBatch = useCallback(async (batchId) => {
    const data = await fetchStorageUnitBatch(batchId);
    setBatchStatusById((prev) => ({ ...prev, [batchId]: data }));
    return data;
  }, []);

  useEffect(() => {
    if (!tracking.length) return undefined;
    const timer = setInterval(async () => {
      for (const batchId of tracking) {
        try {
          const batch = await refreshBatch(batchId);
          if (batch.status === 'COMPLETED' || batch.status === 'FAILED') {
            setTracking((prev) => prev.filter((id) => id !== batchId));
          }
        } catch (_err) {
          setTracking((prev) => prev.filter((id) => id !== batchId));
        }
      }
    }, 3000);

    return () => clearInterval(timer);
  }, [tracking, refreshBatch]);

  const trackBatch = useCallback((batchId) => {
    setTracking((prev) => [...new Set([...prev, batchId])]);
  }, []);

  return { batchStatusById, refreshBatch, trackBatch };
}
