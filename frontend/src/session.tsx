import { createContext, ReactNode, useContext, useEffect, useState } from "react";
import { api, openPeriod, Person, signIn, storedToken, storeToken } from "./api";

type Session = {
  me: Person | null;
  today: string | null;
  period: string | null;
  setPeriod: (period: string) => void;
  refreshClock: () => Promise<void>;
  login: (handle: string) => Promise<void>;
  logout: () => void;
};

const SessionContext = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Person | null>(null);
  const [today, setToday] = useState<string | null>(null);
  const [period, setPeriod] = useState<string | null>(null);

  const refreshClock = async () => {
    const clock = await api<{ today: string }>("/clock", { token: null });
    setToday(clock.today);
    setPeriod((current) => current ?? openPeriod(clock.today));
  };

  useEffect(() => {
    void refreshClock().catch(() => undefined);
    if (storedToken()) {
      api<Person>("/auth/me")
        .then(setMe)
        .catch(() => storeToken(null));
    }
  }, []);

  const login = async (handle: string) => {
    const signed = await signIn(handle);
    storeToken(signed.token);
    const { token: _token, ...person } = signed;
    setMe(person);
  };
  const logout = () => {
    storeToken(null);
    setMe(null);
  };

  return (
    <SessionContext.Provider value={{ me, today, period, setPeriod, refreshClock, login, logout }}>{children}</SessionContext.Provider>
  );
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession outside SessionProvider");
  return session;
}

export function can(me: Person | null, permission: string): boolean {
  return !!me && me.permissions.includes(permission);
}
