import { useState } from 'react';

import { useAuth } from '../contexts/AuthContext';
import { fetchEbayAuthUrl } from '../lib/api';

export function useEbayAuth(userId) {
  const { user } = useAuth();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const connect = async () => {
    setLoading(true);
    setError('');
    try {
      const resolvedUserId = userId || user?.id;
      if (!resolvedUserId) {
        throw new Error('Sign in before connecting a marketplace');
      }
      const data = await fetchEbayAuthUrl(resolvedUserId);
      window.open(data.auth_url, '_blank', 'noopener');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return { loading, error, connect };
}
