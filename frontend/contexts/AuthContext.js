import { createContext, useContext, useEffect, useMemo, useState } from 'react';

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

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const refreshUser = async () => {
    try {
      const currentUser = await fetchCurrentUser();
      setUser(currentUser);
      return currentUser;
    } catch (error) {
      setUser(null);
      return null;
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refreshUser();
  }, []);

  const value = useMemo(
    () => ({
      user,
      loading,
      refreshUser,
      login: async (payload) => {
        const session = await loginUser(payload);
        setUser(session.user);
        return session;
      },
      register: async (payload) => {
        const session = await registerUser(payload);
        setUser(session.user);
        return session;
      },
      logout: async () => {
        await logoutUser();
        setUser(null);
      },
      changePassword: async (payload) => changePassword(payload),
      forgotPassword: async (payload) => forgotPassword(payload),
      resetPassword: async (payload) => {
        const session = await resetPassword(payload);
        setUser(session.user);
        return session;
      },
      setViewAsRegular: async (enabled) => {
        const nextUser = await updateSessionViewMode(enabled);
        setUser(nextUser);
        return nextUser;
      },
    }),
    [loading, user],
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
