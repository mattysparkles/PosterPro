import { useEffect } from 'react';
import { useRouter } from 'next/router';
import { Toaster as HotToaster } from 'react-hot-toast';
import { Toaster } from 'sonner';

import AuthGate from '../components/auth/AuthGate';
import { AdminThemeProvider } from '../contexts/AdminThemeContext';
import { AuthProvider } from '../contexts/AuthContext';
import '../styles/globals.css';

export default function App({ Component, pageProps }) {
  const router = useRouter();

  useEffect(() => {
    if (typeof window === 'undefined' || !('serviceWorker' in navigator)) return;
    // The intake offline helper used to be registered for the entire origin.
    // Its cache-first static-asset strategy could therefore keep an old
    // Listings/navigation bundle alive after deployment.  Keep the helper
    // strictly opt-in for Intake and remove the legacy global registration
    // everywhere else.
    if (router.pathname.startsWith('/intake')) {
      navigator.serviceWorker.register('/intake-offline-sw.js').catch(() => undefined);
      return;
    }
    navigator.serviceWorker.getRegistrations()
      .then((registrations) => Promise.all(
        registrations
          .filter((registration) => registration.active?.scriptURL?.endsWith('/intake-offline-sw.js'))
          .map((registration) => registration.unregister()),
      ))
      .catch(() => undefined);
  }, [router.pathname]);

  return (
    <AuthProvider>
      <AdminThemeProvider>
        {Component.requireAuth ? (
          <AuthGate>
            <Component {...pageProps} />
          </AuthGate>
        ) : (
          <Component {...pageProps} />
        )}
        <Toaster richColors position="top-right" />
        <HotToaster position="bottom-right" />
      </AdminThemeProvider>
    </AuthProvider>
  );
}
