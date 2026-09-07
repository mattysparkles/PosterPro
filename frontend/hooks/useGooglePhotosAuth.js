import { useState } from 'react';

import { useAuth } from '../contexts/AuthContext';
import { fetchGooglePhotosAuthUrl } from '../lib/api';

export function useGooglePhotosAuth() {
  const { user } = useAuth();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const connect = async () => {
    setLoading(true);
    setError('');
    try {
      if (!user?.id) {
        throw new Error('Sign in before connecting Google Photos');
      }
      const data = await fetchGooglePhotosAuthUrl();
      const authUrl = data?.auth_url;
      if (!authUrl) {
        throw new Error('Google Photos authorization URL was not returned');
      }
      window.open(authUrl, '_blank', 'noopener');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return { loading, error, connect };
}
