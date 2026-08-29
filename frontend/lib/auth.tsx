"use client";

/**
 * Real auth. A phone number, an OTP, and a signed session token from the API.
 *
 * The token is the only thing kept in localStorage — the profile is re-fetched
 * from `/auth/me` on load, so a farmer who changes his village on one device
 * sees it on the other. A stale or tampered token 401s and we sign him out
 * rather than showing a half-broken page.
 *
 * Everything outside this file talks to `useAuth()`, which is why swapping
 * localStorage demo auth for this touched one file.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { Language, RiskProfile } from "./types";
import { ApiError, API_BASE } from "./api";

const TOKEN_KEY = "bhavsetu.token";
const LANG_KEY = "bhavsetu.lang";
const PREFIX = `${API_BASE}/api/v1`;

export interface Farmer {
  id: number;
  name: string;
  phone: string;
  village: string;
  district: string;
  language: Language;
  riskProfile: RiskProfile;
  lat: number | null;
  lon: number | null;
  homeMandi: string | null;
  isNew: boolean;
}

export interface OtpChallenge {
  phone: string;
  expiresIn: number;
  channel: string;
  /** Only present while `otp.channel: log` — the demo path, never production. */
  devCode?: string;
}

export interface RegistrationDetails {
  name?: string;
  village?: string;
  district?: string;
  language?: Language;
  riskProfile?: RiskProfile;
}

interface AuthState {
  user: Farmer | null;
  token: string | null;
  ready: boolean;
  language: Language;
  setLanguage: (l: Language) => void;
  requestOtp: (phone: string) => Promise<OtpChallenge>;
  verifyOtp: (phone: string, code: string, details?: RegistrationDetails) => Promise<Farmer>;
  updateProfile: (details: RegistrationDetails) => Promise<Farmer>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

async function call<T>(path: string, init?: RequestInit, token?: string | null): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${PREFIX}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init?.headers ?? {}),
      },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Is the backend running?", "offline");
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    let code = "error";
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (Array.isArray(body?.detail) && body.detail[0]?.msg) detail = body.detail[0].msg;
      code = body?.code ?? code;
    } catch {
      /* keep the status message */
    }
    throw new ApiError(response.status, detail, code);
  }
  return (await response.json()) as T;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<Farmer | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [language, setLanguageState] = useState<Language>("en");
  const [ready, setReady] = useState(false);

  // Restore the session on load. The profile comes from the server, not from
  // whatever this browser last cached — those can disagree.
  useEffect(() => {
    const saved = (() => {
      try {
        return window.localStorage.getItem(TOKEN_KEY);
      } catch {
        return null;
      }
    })();
    try {
      const lang = window.localStorage.getItem(LANG_KEY) as Language | null;
      if (lang) setLanguageState(lang);
    } catch {
      /* language preference is not worth crashing over */
    }

    if (!saved) {
      setReady(true);
      return;
    }

    call<Farmer>("/auth/me", undefined, saved)
      .then((farmer) => {
        setToken(saved);
        setUser(farmer);
        setLanguageState(farmer.language ?? "en");
      })
      .catch(() => {
        // Expired or tampered — clear it rather than leaving a broken session.
        try {
          window.localStorage.removeItem(TOKEN_KEY);
        } catch {
          /* ignore */
        }
      })
      .finally(() => setReady(true));
  }, []);

  const setLanguage = useCallback((next: Language) => {
    setLanguageState(next);
    try {
      window.localStorage.setItem(LANG_KEY, next);
    } catch {
      /* ignore */
    }
  }, []);

  const requestOtp = useCallback(
    (phone: string) => call<OtpChallenge>("/auth/request-otp", {
      method: "POST",
      body: JSON.stringify({ phone }),
    }),
    [],
  );

  const verifyOtp = useCallback(
    async (phone: string, code: string, details: RegistrationDetails = {}) => {
      const body = await call<{ token: string; farmer: Farmer }>("/auth/verify", {
        method: "POST",
        body: JSON.stringify({ phone, code, ...details }),
      });
      setToken(body.token);
      setUser(body.farmer);
      setLanguageState(body.farmer.language ?? "en");
      try {
        window.localStorage.setItem(TOKEN_KEY, body.token);
      } catch {
        /* a private window still gets a working session, just not a durable one */
      }
      return body.farmer;
    },
    [],
  );

  const updateProfile = useCallback(
    async (details: RegistrationDetails) => {
      const farmer = await call<Farmer>("/auth/me", {
        method: "PATCH",
        body: JSON.stringify(details),
      }, token);
      setUser(farmer);
      if (farmer.language) setLanguageState(farmer.language);
      return farmer;
    },
    [token],
  );

  const logout = useCallback(() => {
    setUser(null);
    setToken(null);
    try {
      window.localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* ignore */
    }
  }, []);

  const value = useMemo<AuthState>(
    () => ({ user, token, ready, language, setLanguage, requestOtp, verifyOtp, updateProfile, logout }),
    [user, token, ready, language, setLanguage, requestOtp, verifyOtp, updateProfile, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
