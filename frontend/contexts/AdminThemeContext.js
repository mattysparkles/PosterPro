import { createContext, useContext, useEffect, useMemo, useState } from 'react';

import { DEFAULT_ADMIN_THEME_ID, getAdminThemeById } from '../lib/adminThemes';

const STORAGE_KEY = 'posterpro.adminTheme';

const AdminThemeContext = createContext({
  activeThemeId: DEFAULT_ADMIN_THEME_ID,
  activeTheme: getAdminThemeById(DEFAULT_ADMIN_THEME_ID),
  setThemeId: () => {},
});

function applyTheme(themeId) {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  root.setAttribute('data-admin-theme', themeId || DEFAULT_ADMIN_THEME_ID);
}

export function AdminThemeProvider({ children }) {
  const [activeThemeId, setActiveThemeId] = useState(DEFAULT_ADMIN_THEME_ID);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const stored = window.localStorage.getItem(STORAGE_KEY);
    const resolved = stored || DEFAULT_ADMIN_THEME_ID;
    setActiveThemeId(resolved);
    applyTheme(resolved);
  }, []);

  const setThemeId = (nextThemeId) => {
    const resolved = getAdminThemeById(nextThemeId).id;
    setActiveThemeId(resolved);
    applyTheme(resolved);
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(STORAGE_KEY, resolved);
    }
  };

  const value = useMemo(
    () => ({
      activeThemeId,
      activeTheme: getAdminThemeById(activeThemeId),
      setThemeId,
    }),
    [activeThemeId],
  );

  return <AdminThemeContext.Provider value={value}>{children}</AdminThemeContext.Provider>;
}

export function useAdminThemeContext() {
  return useContext(AdminThemeContext);
}
