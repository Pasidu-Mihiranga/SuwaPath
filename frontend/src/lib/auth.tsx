import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, tokens } from "./api";

export type Role =
  | "patient"
  | "guardian"
  | "doctor"
  | "hospital_admin"
  | "system_admin";

export interface AppUser {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  preferred_language: string;
  hospital_id?: string | null;
  patient_profile?: {
    age?: number | null;
    sex?: string | null;
    city?: string | null;
    is_pregnant?: boolean;
    accessibility_large_text?: boolean;
    chronic_conditions?: string[];
    allergies?: string[];
  } | null;
  doctor_profile?: {
    id: string;
    specialty_name?: string | null;
    hospital_name?: string | null;
  } | null;
}

interface AuthValue {
  user: AppUser | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<AppUser>;
  logout: () => void;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AppUser | null>(null);
  const [loading, setLoading] = useState(true);

  const loadUser = useCallback(async () => {
    if (!tokens.access) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      const { data } = await api.get<AppUser>("/auth/me");
      setUser(data);
    } catch {
      tokens.clear();
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadUser();
  }, [loadUser]);

  // The elderly pathway asks for larger type and bigger tap targets. This is
  // driven entirely by design tokens: setting data-density on :root rescales
  // the type ramp and control sizing, so no component needs a special case.
  useEffect(() => {
    if (user?.patient_profile?.accessibility_large_text) {
      document.documentElement.setAttribute("data-density", "comfortable");
    } else {
      document.documentElement.removeAttribute("data-density");
    }
  }, [user]);

  const login = useCallback(async (email: string, password: string) => {
    const { data } = await api.post("/auth/login", { email, password });
    tokens.set(data.access_token, data.refresh_token);
    setUser(data.user);
    return data.user as AppUser;
  }, []);

  const logout = useCallback(() => {
    tokens.clear();
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ user, loading, login, logout, refreshUser: loadUser }),
    [user, loading, login, logout, loadUser],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}

export const HOME_BY_ROLE: Record<Role, string> = {
  patient: "/patient",
  guardian: "/guardian",
  doctor: "/doctor",
  hospital_admin: "/hospital",
  system_admin: "/admin",
};
