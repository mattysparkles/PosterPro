import { useEffect } from 'react';
import { useRouter } from 'next/router';
import { Toaster as HotToaster } from 'react-hot-toast';
import { Toaster } from 'sonner';

import AuthGate from '../components/auth/AuthGate';
import ErrorBoundary from '../components/ErrorBoundary';
import { AdminThemeProvider } from '../contexts/AdminThemeContext';
import { AuthProvider } from '../contexts/AuthContext';
import { matomoPageUrl, matomoPush } from '../lib/analytics';
import '../styles/globals.css';

export default function App({ Component, pageProps }) {
  const router = useRouter();

  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const trackerUrl = '//piwik.sparkleserver.site/';
    const queue = window._paq = window._paq || [];
    queue.push(['setTrackerUrl', `${trackerUrl}matomo.php`]);
    queue.push(['setSiteId', '16']);
    queue.push(['enableLinkTracking']);
    queue.push(['setCustomUrl', matomoPageUrl(window.location.href)]);
    queue.push(['setDocumentTitle', document.title]);
    queue.push(['trackPageView']);

    if (!document.querySelector('script[data-posterpro-matomo="true"]')) {
      const script = document.createElement('script');
      script.async = true;
      script.src = `${trackerUrl}matomo.js`;
      script.dataset.posterproMatomo = 'true';
      document.head.appendChild(script);
    }

    const onRouteChange = (url) => {
      queue.push(['setCustomUrl', matomoPageUrl(url, window.location.origin)]);
      queue.push(['setDocumentTitle', document.title]);
      queue.push(['trackPageView']);
    };
    const onClick = (event) => {
      const target = event.target instanceof Element ? event.target.closest('a,button,[role="button"]') : null;
      if (!target || target.closest('[data-no-analytics="true"]')) return;
      const explicit = target.getAttribute('data-analytics-label') || target.getAttribute('aria-label');
      const safeText = explicit || (target.tagName === 'BUTTON' ? target.textContent : '');
      const label = String(safeText || target.tagName.toLowerCase()).replace(/\s+/g, ' ').trim().slice(0, 80);
      const href = target instanceof HTMLAnchorElement ? target.pathname : '';
      matomoPush(['trackEvent', 'UI', 'click', label || 'control', href]);
    };
    document.addEventListener('click', onClick, true);
    router.events.on('routeChangeComplete', onRouteChange);
    return () => {
      document.removeEventListener('click', onClick, true);
      router.events.off('routeChangeComplete', onRouteChange);
    };
  }, [router.events]);

  useEffect(() => {
    if (typeof window === 'undefined' || !('serviceWorker' in navigator)) return;
    // Capture and packing are deliberately the only offline-enabled workflows.
    // The worker is network-first and scopes its cache to these routes/assets,
    // so it does not freeze catalog or publishing bundles after a deployment.
    if (!router.pathname.startsWith('/intake') && !router.pathname.startsWith('/packing')) return;
    navigator.serviceWorker.register('/intake-offline-sw.js', { scope: '/' }).catch(() => undefined);
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
