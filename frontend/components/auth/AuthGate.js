import { useRouter } from 'next/router';
import { useEffect } from 'react';

import { useAuth } from '../../contexts/AuthContext';

export default function AuthGate({ children }) {
  const router = useRouter();
  const { user, loading, bootError, refreshUser } = useAuth();

  useEffect(() => {
    if (!loading && !user && !bootError) {
      const next = encodeURIComponent(router.asPath || '/app');
      router.replace(`/login?next=${next}`);
    }
  }, [bootError, loading, router, user]);

  if (loading || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-6 text-foreground">
        <div className="w-full max-w-md rounded-[2rem] border border-border/70 bg-card p-8 text-center shadow-soft">
          <p className="text-xs font-bold uppercase tracking-[0.3em] text-primary">PosterPro</p>
          <h1 className="mt-3 text-2xl font-semibold">Checking your workspace</h1>
          {bootError ? (
            <>
              <p className="mt-3 text-sm text-muted-foreground">
                {bootError}
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
            <p className="mt-3 text-sm text-muted-foreground">
              Loading your account and routing you into the reseller dashboard.
            </p>
          )}
        </div>
      </div>
    );
  }

  return children;
}
