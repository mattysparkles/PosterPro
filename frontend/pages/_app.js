import { useEffect, useState } from 'react';
import { Toaster as HotToaster } from 'react-hot-toast';
import { Toaster } from 'sonner';

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
    <>
      <Component {...pageProps} theme={theme} setTheme={setTheme} />
      <Toaster richColors position="top-right" />
      <HotToaster position="bottom-right" />
    </>
  );
}
