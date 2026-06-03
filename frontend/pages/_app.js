import { Toaster as HotToaster } from 'react-hot-toast';
import { Toaster } from 'sonner';

import AuthGate from '../components/auth/AuthGate';
import { AdminThemeProvider } from '../contexts/AdminThemeContext';
import { AuthProvider } from '../contexts/AuthContext';
import '../styles/globals.css';

export default function App({ Component, pageProps }) {
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
