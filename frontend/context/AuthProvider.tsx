"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, type UserProfile } from "@/lib/api";
import { clearStoredToken, getStoredToken, setStoredToken } from "@/lib/auth-storage";

type AuthContextValue = {
  user: UserProfile | null;
  loading: boolean;
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, displayName?: string) => Promise<void>;
  logout: () => void;
  refresh: () => Promise<void>;
  updateProfile: (displayName: string) => Promise<void>;
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const token = getStoredToken();
    if (!token) {
      setUser(null);
      return;
    }
    try {
      setUser(await api.authMe());
    } catch {
      clearStoredToken();
      setUser(null);
    }
  }, []);

  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, [refresh]);

  const login = useCallback(async (email: string, password: string) => {
    const { access_token } = await api.authLogin(email, password);
    setStoredToken(access_token);
    await refresh();
  }, [refresh]);

  const register = useCallback(
    async (email: string, password: string, displayName?: string) => {
      const { access_token } = await api.authRegister(email, password, displayName);
      setStoredToken(access_token);
      await refresh();
    },
    [refresh],
  );

  const logout = useCallback(() => {
    clearStoredToken();
    setUser(null);
  }, []);

  const updateProfile = useCallback(
    async (displayName: string) => {
      const updated = await api.authUpdateMe({ display_name: displayName });
      setUser(updated);
    },
    [],
  );

  const changePassword = useCallback(
    async (currentPassword: string, newPassword: string) => {
      await api.authChangePassword(currentPassword, newPassword);
    },
    [],
  );

  const value = useMemo(
    () => ({
      user,
      loading,
      isAuthenticated: !!user,
      login,
      register,
      logout,
      refresh,
      updateProfile,
      changePassword,
    }),
    [user, loading, login, register, logout, refresh, updateProfile, changePassword],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
