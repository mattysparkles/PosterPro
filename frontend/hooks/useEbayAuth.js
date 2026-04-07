import { useState } from 'react';

import { fetchEbayAuthUrl } from '../lib/api';

export function useEbayAuth(userId = 1) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const connect = async () => {
    setLoading(true);
    setError('');
    try {
      const redirect = `${window.location.origin}/api/ebay/callback`;
      const data = await fetchEbayAuthUrl(userId, redirect);
      window.open(data.auth_url, '_blank', 'noopener');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return { loading, error, connect };
}
