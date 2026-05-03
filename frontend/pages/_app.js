import { useEffect, useState } from 'react';
import { Toaster as HotToaster } from 'react-hot-toast';
import { Toaster } from 'sonner';

import AuthGate from '../components/auth/AuthGate';
import { AuthProvider } from '../contexts/AuthContext';
import '../styles/globals.css';

export default function App({ Component, pageProps }) {
  const [theme, setTheme] = useState('light');

  useEffect(() => {
    const stored = localStorage.getItem('posterpro-theme');
    const initial = stored || 'light';
    setTheme(initial);
    document.documentElement.classList.toggle('dark', initial === 'dark');
  }, []);

  return (
    <AuthProvider>
      {Component.requireAuth ? (
        <AuthGate>
          <Component {...pageProps} theme={theme} setTheme={setTheme} />
        </AuthGate>
      ) : (
        <Component {...pageProps} theme={theme} setTheme={setTheme} />
      )}
      <Toaster richColors position="top-right" />
      <HotToaster position="bottom-right" />
    </AuthProvider>
  );
}
