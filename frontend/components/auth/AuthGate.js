import { useRouter } from 'next/router';
import { useEffect, useState } from 'react';

import { useAuth } from '../../contexts/AuthContext';

export default function AuthGate({ children }) {
  const router = useRouter();
  const { user, loading, bootError, refreshUser } = useAuth();
  const [bootTooSlow, setBootTooSlow] = useState(false);

  useEffect(() => {
    if (!loading && !user && !bootError) {
      const next = encodeURIComponent(router.asPath || '/app');
      router.replace(`/login?next=${next}`);
    }
  }, [bootError, loading, router, user]);

  useEffect(() => {
    if (!loading || user || bootError) {
      setBootTooSlow(false);
      return undefined;
    }

    const timer = setTimeout(() => {
      setBootTooSlow(true);
    }, 8000);

    return () => clearTimeout(timer);
  }, [bootError, loading, user]);

  if (!user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-6 text-foreground">
        <div className="w-full max-w-md rounded-[2rem] border border-border/70 bg-card p-8 text-center shadow-soft">
          <p className="text-xs font-bold uppercase tracking-[0.3em] text-primary">PosterPro</p>
          <h1 className="mt-3 text-2xl font-semibold">Checking your workspace</h1>
          {bootError || bootTooSlow ? (
            <>
              <p className="mt-3 text-sm text-muted-foreground">
                {bootError || 'Session check is taking longer than expected. You can retry the workspace check or go straight to login.'}
              </p>
              <button
                className="mt-5 inline-flex items-center justify-center rounded-[12px] bg-primary px-4 py-2 text-sm font-semibold text-white"
                type="button"
                onClick={() => refreshUser()}
              >
                Retry
              </button>
              <button
                className="mt-3 inline-flex items-center justify-center rounded-[12px] border border-border/70 bg-transparent px-4 py-2 text-sm font-semibold text-foreground"
                type="button"
                onClick={() => {
                  const next = encodeURIComponent(router.asPath || '/app');
                  router.replace(`/login?next=${next}`);
                }}
              >
                Go to login
              </button>
            </>
          ) : (
            <>
              <p className="mt-3 text-sm text-muted-foreground">
                Loading your account and routing you into the reseller dashboard.
              </p>
              <button
                className="mt-5 inline-flex items-center justify-center rounded-[12px] border border-border/70 bg-transparent px-4 py-2 text-sm font-semibold text-foreground"
                type="button"
                onClick={() => {
                  const next = encodeURIComponent(router.asPath || '/app');
                  router.replace(`/login?next=${next}`);
                }}
              >
                Return to login
              </button>
            </>
          )}
        </div>
      </div>
    );
  }

  return children;
}
