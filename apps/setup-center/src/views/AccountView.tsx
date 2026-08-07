import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { CheckCircle2, ExternalLink, Loader2, LogOut, RefreshCw, ShieldAlert, UserRound } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { safeFetch } from "../providers";
import { openExternalUrl } from "../platform";

type AccountSnapshot = {
  account_user_id?: string;
  status: string;
  status_reason?: string | null;
  fetched_at?: string;
  profile?: {
    email?: string;
    name?: string;
    preferred_username?: string;
  };
  entitlements?: unknown;
};

type LoginStart = {
  attempt_id: string;
  authorization_url: string;
};

type Props = {
  serviceRunning: boolean;
  apiBaseUrl: string;
};

function entitlementCount(value: unknown): number {
  if (Array.isArray(value)) return value.length;
  if (value && typeof value === "object") {
    const nested = (value as { entitlements?: unknown }).entitlements;
    if (Array.isArray(nested)) return nested.length;
  }
  return 0;
}

export function AccountView({ serviceRunning, apiBaseUrl }: Props) {
  const { t } = useTranslation();
  const [snapshot, setSnapshot] = useState<AccountSnapshot>({ status: "signed_out" });
  const [busy, setBusy] = useState<"login" | "refresh" | "logout" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollingRef.current) clearInterval(pollingRef.current);
    pollingRef.current = null;
  }, []);

  const loadStatus = useCallback(async () => {
    if (!serviceRunning) return;
    const response = await safeFetch(`${apiBaseUrl}/api/account/status`);
    setSnapshot(await response.json());
  }, [apiBaseUrl, serviceRunning]);

  useEffect(() => {
    void loadStatus().catch((reason) => setError(String(reason)));
    return stopPolling;
  }, [loadStatus, stopPolling]);

  const startLogin = useCallback(async () => {
    setBusy("login");
    setError(null);
    stopPolling();
    try {
      const response = await safeFetch(`${apiBaseUrl}/api/account/login/start`, { method: "POST" });
      const attempt = await response.json() as LoginStart;
      await openExternalUrl(attempt.authorization_url);
      pollingRef.current = setInterval(async () => {
        try {
          const poll = await safeFetch(`${apiBaseUrl}/api/account/login/status/${encodeURIComponent(attempt.attempt_id)}`);
          const result = await poll.json() as { status: string; error?: string };
          if (result.status === "complete") {
            stopPolling();
            await loadStatus();
            setBusy(null);
          } else if (result.status === "failed" || result.status === "expired") {
            stopPolling();
            setError(result.error || t("account.loginExpired"));
            setBusy(null);
          }
        } catch (reason) {
          stopPolling();
          setError(String(reason));
          setBusy(null);
        }
      }, 1_000);
    } catch (reason) {
      setError(String(reason));
      setBusy(null);
    }
  }, [apiBaseUrl, loadStatus, stopPolling, t]);

  const refreshEntitlements = useCallback(async () => {
    setBusy("refresh");
    setError(null);
    try {
      await safeFetch(`${apiBaseUrl}/api/account/entitlements/refresh`, { method: "POST" });
      await loadStatus();
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(null);
    }
  }, [apiBaseUrl, loadStatus]);

  const logout = useCallback(async () => {
    setBusy("logout");
    setError(null);
    stopPolling();
    try {
      const response = await safeFetch(`${apiBaseUrl}/api/account/logout`, { method: "POST" });
      const result = await response.json() as { end_session_url?: string };
      await loadStatus();
      if (result.end_session_url) await openExternalUrl(result.end_session_url);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(null);
    }
  }, [apiBaseUrl, loadStatus, stopPolling]);

  if (!serviceRunning) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-muted-foreground">
        <UserRound size={48} aria-hidden="true" />
        <p>{t("account.serviceRequired")}</p>
      </div>
    );
  }

  const signedIn = snapshot.status !== "signed_out";
  const active = snapshot.status === "active";
  const displayName = snapshot.profile?.name || snapshot.profile?.preferred_username || snapshot.profile?.email;

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-5 p-6">
      <div>
        <h1 className="text-2xl font-semibold">{t("account.title")}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{t("account.description")}</p>
      </div>

      {error && (
        <div role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      <Card>
        <CardContent className="flex flex-col gap-5 p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="flex items-center gap-3">
              {active ? <CheckCircle2 className="text-emerald-500" aria-hidden="true" /> : <ShieldAlert className="text-amber-500" aria-hidden="true" />}
              <div>
                <div className="font-medium">{signedIn ? (displayName || snapshot.account_user_id) : t("account.signedOut")}</div>
                <div className="text-sm text-muted-foreground">
                  {signedIn ? t("account.statusValue", { status: snapshot.status }) : t("account.signInHint")}
                </div>
              </div>
            </div>
            {!signedIn ? (
              <Button onClick={() => void startLogin()} disabled={busy !== null}>
                {busy === "login" ? <Loader2 className="animate-spin" aria-hidden="true" /> : <ExternalLink aria-hidden="true" />}
                {t("account.signIn")}
              </Button>
            ) : (
              <Button variant="outline" onClick={() => void logout()} disabled={busy !== null}>
                {busy === "logout" ? <Loader2 className="animate-spin" aria-hidden="true" /> : <LogOut aria-hidden="true" />}
                {t("account.signOut")}
              </Button>
            )}
          </div>

          {signedIn && (
            <div className="grid gap-3 rounded-lg border p-4 sm:grid-cols-2">
              <div>
                <div className="text-xs uppercase tracking-wide text-muted-foreground">{t("account.email")}</div>
                <div className="mt-1 break-all text-sm">{snapshot.profile?.email || "—"}</div>
              </div>
              <div>
                <div className="text-xs uppercase tracking-wide text-muted-foreground">{t("account.entitlements")}</div>
                <div className="mt-1 text-sm">{entitlementCount(snapshot.entitlements)}</div>
              </div>
              {snapshot.status_reason && (
                <div className="sm:col-span-2">
                  <div className="text-xs uppercase tracking-wide text-muted-foreground">{t("account.statusReason")}</div>
                  <div className="mt-1 text-sm">{snapshot.status_reason}</div>
                </div>
              )}
            </div>
          )}

          {signedIn && (
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" onClick={() => void refreshEntitlements()} disabled={busy !== null || !active}>
                {busy === "refresh" ? <Loader2 className="animate-spin" aria-hidden="true" /> : <RefreshCw aria-hidden="true" />}
                {t("account.refreshEntitlements")}
              </Button>
              {snapshot.fetched_at && <span className="self-center text-xs text-muted-foreground">{t("account.lastSync", { time: new Date(snapshot.fetched_at).toLocaleString() })}</span>}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
