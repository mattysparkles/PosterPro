import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react';

import {
  changePassword,
  fetchCurrentUser,
  forgotPassword,
  loginUser,
  logoutUser,
  registerUser,
  resetPassword,
  updateSessionViewMode,
} from '../lib/api';

const AuthContext = createContext(null);
const RECENT_LOGIN_CACHE_KEY = 'posterpro_recent_login_user';
const RECENT_LOGIN_MAX_AGE_MS = 5 * 60 * 1000;

function readRecentLoginUser() {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.sessionStorage.getItem(RECENT_LOGIN_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    const ts = Number(parsed?.ts || 0);
    if (!parsed?.user || !ts || Date.now() - ts > RECENT_LOGIN_MAX_AGE_MS) {
      window.sessionStorage.removeItem(RECENT_LOGIN_CACHE_KEY);
      return null;
    }
    return parsed.user;
  } catch {
    return null;
  }
}

function cacheRecentLoginUser(user) {
  if (typeof window === 'undefined') return;
  try {
    if (!user) {
      window.sessionStorage.removeItem(RECENT_LOGIN_CACHE_KEY);
      return;
    }
    window.sessionStorage.setItem(
      RECENT_LOGIN_CACHE_KEY,
      JSON.stringify({ ts: Date.now(), user }),
    );
  } catch {
    // best-effort cache
  }
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => readRecentLoginUser());
  const [loading, setLoading] = useState(true);
  const [bootError, setBootError] = useState(null);
  const [bootAttempt, setBootAttempt] = useState(0);
  const refreshRequestIdRef = useRef(0);

  const refreshUser = async () => {
    const requestId = refreshRequestIdRef.current + 1;
    refreshRequestIdRef.current = requestId;
    setLoading(true);
    setBootError(null);
    setBootAttempt((value) => value + 1);
    try {
      const currentUser = await fetchCurrentUser();
      if (refreshRequestIdRef.current !== requestId) return null;
      setUser(currentUser);
      cacheRecentLoginUser(currentUser);
      return currentUser;
    } catch (error) {
      if (refreshRequestIdRef.current !== requestId) return null;
      setUser(null);
      cacheRecentLoginUser(null);
      setBootError(error?.message || 'Unable to load your session.');
      return null;
    } finally {
      if (refreshRequestIdRef.current !== requestId) return;
      setLoading(false);
    }
  };

  useEffect(() => {
    refreshUser();
  }, []);

  useEffect(() => {
    if (!loading) return undefined;
    const timeoutMs = Number(process.env.NEXT_PUBLIC_API_TIMEOUT_MS || 15000);
    const watchdogMs = Math.max(5000, Math.min(60000, timeoutMs + 5000));
    const timer = setTimeout(() => {
      setBootError((current) => current || 'Session check is taking too long. Please retry.');
      setLoading(false);
    }, watchdogMs);
    return () => clearTimeout(timer);
  }, [bootAttempt, loading]);

  const value = useMemo(
    () => ({
      user,
      loading,
      bootError,
      refreshUser,
      login: async (payload) => {
        refreshRequestIdRef.current += 1;
        const session = await loginUser(payload);
        setBootError(null);
        setLoading(false);
        setUser(session.user);
        cacheRecentLoginUser(session.user);
        return session;
      },
      register: async (payload) => {
        refreshRequestIdRef.current += 1;
        const session = await registerUser(payload);
        setBootError(null);
        setLoading(false);
        setUser(session.user);
        cacheRecentLoginUser(session.user);
        return session;
      },
      logout: async () => {
        await logoutUser();
        setUser(null);
        cacheRecentLoginUser(null);
      },
      changePassword: async (payload) => changePassword(payload),
      forgotPassword: async (payload) => forgotPassword(payload),
      resetPassword: async (payload) => {
        refreshRequestIdRef.current += 1;
        const session = await resetPassword(payload);
        setBootError(null);
        setLoading(false);
        setUser(session.user);
        cacheRecentLoginUser(session.user);
        return session;
      },
      setViewAsRegular: async (enabled) => {
        const nextUser = await updateSessionViewMode(enabled);
        setUser(nextUser);
        cacheRecentLoginUser(nextUser);
        return nextUser;
      },
    }),
    [bootError, loading, user],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return context;
}
