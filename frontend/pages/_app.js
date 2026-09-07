import { useEffect } from 'react';
import { useRouter } from 'next/router';
import { Toaster as HotToaster } from 'react-hot-toast';
import { Toaster } from 'sonner';

import AuthGate from '../components/auth/AuthGate';
import ErrorBoundary from '../components/ErrorBoundary';
import { AdminThemeProvider } from '../contexts/AdminThemeContext';
import { AuthProvider } from '../contexts/AuthContext';
import '../styles/globals.css';

export default function App({ Component, pageProps }) {
  const router = useRouter();

  useEffect(() => {
    if (typeof window === 'undefined' || !('serviceWorker' in navigator)) return;
    // Do not keep an origin-wide or route-wide service worker active while the
    // shell is being actively iterated. Stale caches can preserve old bundles
    // and surface client-side exceptions even after a successful deploy.
    navigator.serviceWorker.getRegistrations()
      .then((registrations) => Promise.all(
        registrations
          .map((registration) => registration.unregister()),
      ))
      .catch(() => undefined);
    if (window.caches?.keys) {
      window.caches.keys().then((keys) => Promise.all(keys.map((key) => window.caches.delete(key)))).catch(() => undefined);
    }
  }, [router.pathname]);

  return (
    <AuthProvider>
      <AdminThemeProvider>
        <ErrorBoundary>
          {Component.requireAuth ? (
            <AuthGate>
              <Component {...pageProps} />
            </AuthGate>
          ) : (
            <Component {...pageProps} />
          )}
          <Toaster richColors position="top-right" />
          <HotToaster position="bottom-right" />
        </ErrorBoundary>
      </AdminThemeProvider>
    </AuthProvider>
  );
}
