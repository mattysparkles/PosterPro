import { useRouter } from 'next/router';
import { useEffect } from 'react';

import { useAuth } from '../../contexts/AuthContext';

export default function AuthGate({ children }) {
  const router = useRouter();
  const { user, loading } = useAuth();

  useEffect(() => {
    if (!loading && !user) {
      const next = encodeURIComponent(router.asPath || '/app');
      router.replace(`/login?next=${next}`);
    }
  }, [loading, router, user]);

  if (loading || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-6 text-foreground">
        <div className="w-full max-w-md rounded-[2rem] border border-border/70 bg-card p-8 text-center shadow-soft">
          <p className="text-xs font-bold uppercase tracking-[0.3em] text-primary">PosterPro</p>
          <h1 className="mt-3 text-2xl font-semibold">Checking your workspace</h1>
          <p className="mt-3 text-sm text-muted-foreground">
            Loading your account and routing you into the reseller dashboard.
          </p>
        </div>
      </div>
    );
  }

  return children;
}
