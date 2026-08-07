import axios from "axios";

export const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

export const api = axios.create({ baseURL: `${API_BASE}/api/v1` });

const ACCESS = "suwapath.access";
const REFRESH = "suwapath.refresh";

export const tokens = {
  get access() {
    return localStorage.getItem(ACCESS);
  },
  get refresh() {
    return localStorage.getItem(REFRESH);
  },
  set(access: string, refresh: string) {
    localStorage.setItem(ACCESS, access);
    localStorage.setItem(REFRESH, refresh);
  },
  clear() {
    localStorage.removeItem(ACCESS);
    localStorage.removeItem(REFRESH);
  },
};

api.interceptors.request.use((config) => {
  const token = tokens.access;
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// One silent refresh attempt on 401, then fall back to the login screen.
let refreshing: Promise<string | null> | null = null;

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config;
    if (error.response?.status !== 401 || original?._retried || !tokens.refresh) {
      return Promise.reject(error);
    }
    original._retried = true;

    refreshing ??= axios
      .post(`${API_BASE}/api/v1/auth/refresh`, { refresh_token: tokens.refresh })
      .then((r) => {
        tokens.set(r.data.access_token, r.data.refresh_token);
        return r.data.access_token as string;
      })
      .catch(() => {
        tokens.clear();
        return null;
      })
      .finally(() => {
        refreshing = null;
      });

    const token = await refreshing;
    if (!token) {
      window.location.href = "/login";
      return Promise.reject(error);
    }
    original.headers.Authorization = `Bearer ${token}`;
    return api(original);
  },
);

/** Human-readable message from a FastAPI error response. */
export function errorMessage(error: unknown, fallback = "Something went wrong."): string {
  const detail = (error as any)?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg;
  return fallback;
}
