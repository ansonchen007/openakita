import { MarketplacePluginSetup } from "./MarketplacePluginSetup";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { AlertCircle, CheckCircle2, Download, Loader2, PackageCheck, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { MarketplaceInstallProgress, type InstallationProgress } from "./MarketplaceInstallProgress";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { getCurrentDeepLinks, IS_TAURI, onDeepLinkOpen } from "../platform";
import { desktopAccountHeaders, openMarketplaceWithAccount } from "../marketplace/open";
import { safeFetchResponse } from "../providers";
import {
  buildMarketplaceContextUrlFromDeepLink,
  hasMarketplaceClientVersion,
  marketplaceDeepLinkAction,
} from "../marketplace/navigation";

type InstallJob = InstallationProgress & {
  id: string;
  status: "ready" | "downloading" | "verifying" | "installing" | "installed" | "failed" | "cancelled";
  progress: number | null;
  resource_name: string;
  resource_type: "plugin" | "skill" | "mcp";
  version: string;
  permissions: string[];
  dependencies: string[];
  failure_code?: string;
  failure_detail?: string;
  failure_reason?: string;
  failure_stage?: string;
  restart_required?: boolean;
  skill_enabled?: boolean;
  plugin_id?: string;
  resource_slug?: string;
  install_action?: "install" | "already_installed" | "upgrade" | "downgrade" | "replace";
  installed_version?: string;
  installed_pending_restart?: boolean;
  already_installed?: boolean;
};

type ParsedLink = { token: string; endpoint: string };

function parseInstallLink(value: string): ParsedLink | null {
  try {
    const url = new URL(value);
    const token = (url.searchParams.get("token") || "").toLowerCase();
    const endpoint = url.searchParams.get("endpoint") || "";
    if (url.protocol !== "openakita:" || url.hostname !== "marketplace" || url.pathname !== "/install") return null;
    if (!/^[a-f0-9]{64}$/.test(token) || !endpoint) return null;
    const source = new URL(endpoint);
    const local = source.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(source.hostname);
    if (source.protocol !== "https:" && !local) return null;
    if (source.username || source.password || source.search || source.hash || (source.pathname !== "/" && source.pathname !== "")) return null;
    return { token, endpoint: source.origin };
  } catch {
    return null;
  }
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await safeFetchResponse(url, { ...init, headers: { "Content-Type": "application/json", ...await desktopAccountHeaders(), ...(init?.headers || {}) } });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const code = payload?.detail?.code
      || ([401, 403].includes(response.status) ? "marketplace_account_required" : "marketplace_connection_failed");
    throw new Error(code);
  }
  return payload.data as T;
}

export function MarketplaceInstallDialog({
  apiBaseUrl,
  desktopVersion,
}: {
  apiBaseUrl: string;
  desktopVersion: string;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [pluginBusy, setPluginBusy] = useState(false);
  const [job, setJob] = useState<InstallJob | null>(null);
  const [errorCode, setErrorCode] = useState("");
  const recentlyHandled = useRef(new Map<string, number>());
  const pendingOpenLinks = useRef(new Set<string>());
  const notifiedInstalls = useRef(new Set<string>());

  const friendlyError = useCallback((code: string) => t(`marketplaceInstall.errors.${code}`, {
    defaultValue: t("marketplaceInstall.errors.marketplace_install_failed"),
  }), [t]);

  const prepare = useCallback(async (raw: string) => {
    if (!IS_TAURI) return;
    const parsed = parseInstallLink(raw);
    setOpen(true);
    setJob(null);
    setErrorCode("");
    if (!parsed) {
      setErrorCode("marketplace_instruction_invalid");
      return;
    }
    setLoading(true);
    const deadline = Date.now() + 120_000;
    while (Date.now() < deadline) {
      try {
        const prepared = await requestJson<InstallJob>(`${apiBaseUrl}/api/marketplace/installs/prepare`, {
          method: "POST", body: JSON.stringify(parsed),
        });
        setJob(prepared);
        setLoading(false);
        return;
      } catch (error) {
        const code = error instanceof Error ? error.message : "marketplace_connection_failed";
        if (code !== "marketplace_connection_failed") {
          setErrorCode(code);
          setLoading(false);
          return;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 1500));
      }
    }
    setErrorCode("marketplace_connection_failed");
    setLoading(false);
  }, [apiBaseUrl]);

  const handleDeepLink = useCallback((raw: string) => {
    const action = marketplaceDeepLinkAction(raw);
    if (action === "open" && !hasMarketplaceClientVersion(desktopVersion)) {
      pendingOpenLinks.current.add(raw);
      return;
    }

    const now = Date.now();
    const previous = recentlyHandled.current.get(raw) || 0;
    if (now - previous < 1_500) return;
    recentlyHandled.current.set(raw, now);
    if (recentlyHandled.current.size > 100) {
      for (const [value, timestamp] of recentlyHandled.current) {
        if (now - timestamp >= 1_500) recentlyHandled.current.delete(value);
      }
    }

    if (action === "install") {
      void prepare(raw);
      return;
    }
    if (action !== "open") return;

    const target = buildMarketplaceContextUrlFromDeepLink(raw, desktopVersion);
    if (!target) {
      toast.error(t("marketplaceInstall.openLinkInvalid"));
      return;
    }
    const context = new URL(target);
    void openMarketplaceWithAccount(desktopVersion, apiBaseUrl, context.searchParams.get("next") || "/", context.origin).catch(() => {
      toast.error(t("marketplaceInstall.openFailed"));
    });
  }, [desktopVersion, apiBaseUrl, prepare, t]);

  useEffect(() => {
    if (!IS_TAURI) return;
    let disposed = false;
    let cleanup = () => {};
    void getCurrentDeepLinks().then((urls) => urls.forEach(handleDeepLink));
    void onDeepLinkOpen((urls) => urls.forEach(handleDeepLink)).then((unlisten) => {
      if (disposed) unlisten(); else cleanup = unlisten;
    });
    return () => { disposed = true; cleanup(); };
  }, [handleDeepLink]);

  useEffect(() => {
    if (!hasMarketplaceClientVersion(desktopVersion)) return;
    const pending = [...pendingOpenLinks.current];
    pendingOpenLinks.current.clear();
    pending.forEach(handleDeepLink);
  }, [desktopVersion, handleDeepLink]);

  useEffect(() => {
    if (job?.status !== "installed") return;
    const eventName = {
      skill: "openakita:skills-changed",
      plugin: "openakita:plugin-apps-changed",
      mcp: "openakita:mcp-changed",
    }[job.resource_type];
    if (!eventName) return;
    const key = `${apiBaseUrl}/${job.id}`;
    if (notifiedInstalls.current.has(key)) return;
    notifiedInstalls.current.add(key);
    // Installation has already refreshed the backend. Refresh local consumers even
    // when the WebSocket is disconnected; repeated job responses need only one event.
    window.dispatchEvent(new CustomEvent(eventName, {
      detail: { action: "install" },
    }));
  }, [apiBaseUrl, job]);

  useEffect(() => {
    if (!open || !job || ["ready", "installed", "failed", "cancelled"].includes(job.status)) return;
    let cancelled = false;
    let timer = 0;
    const poll = async () => {
      try {
        const next = await requestJson<InstallJob>(`${apiBaseUrl}/api/marketplace/installs/${encodeURIComponent(job.id)}`);
        if (!cancelled) setJob(next);
      } catch {
        if (!cancelled) setErrorCode("marketplace_connection_failed");
      }
      if (!cancelled) timer = window.setTimeout(poll, 700);
    };
    timer = window.setTimeout(poll, 500);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [apiBaseUrl, job, open]);

  async function confirm() {
    if (!job || confirming) return;
    setConfirming(true);
    setErrorCode("");
    try {
      const next = await requestJson<InstallJob>(`${apiBaseUrl}/api/marketplace/installs/${encodeURIComponent(job.id)}/confirm`, { method: "POST" });
      setJob(next);
      if (next.status === "ready") setErrorCode("marketplace_install_state_changed");
    } catch (error) {
      setErrorCode(error instanceof Error ? error.message : "marketplace_install_failed");
    } finally {
      setConfirming(false);
    }
  }

  const active = !!job && ["downloading", "verifying", "installing"].includes(job.status);
  const canInstall = job?.status === "ready" && job.install_action !== "downgrade";

  const close = useCallback(async () => {
    if (active || confirming || pluginBusy) return;
    const current = job;
    setOpen(false);
    if (current?.status !== "ready") return;
    try {
      await requestJson<InstallJob>(`${apiBaseUrl}/api/marketplace/installs/${encodeURIComponent(current.id)}/cancel`, { method: "POST" });
    } catch {
      // The local service persists the pending cancellation and retries delivery.
    }
  }, [active, confirming, pluginBusy, apiBaseUrl, job]);

  const statusLabel = job ? t(`marketplaceInstall.status.${job.status}`) : "";
  let completionKey = job?.restart_required ? "completedRestart" : "completed";
  if (job?.resource_type === "skill") {
    if (job.skill_enabled === false) completionKey = "completedSkillDisabled";
    else if (job.skill_enabled === true) {
      completionKey = job.restart_required ? "completedSkillRestart" : "completedSkillEnabled";
    } else completionKey = "completedSkillUnknown";
  }
  if (job?.already_installed) completionKey = job.installed_pending_restart ? "alreadyInstalledPending" : "alreadyInstalled";

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) void close(); }}>
      <DialogContent className="sm:max-w-[520px] max-h-[90dvh] overflow-y-auto" showCloseButton={!active && !confirming && !pluginBusy}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {job?.status === "installed" && job.resource_type !== "plugin" ? <CheckCircle2 className="text-emerald-500" size={22} /> : <PackageCheck className="text-blue-600" size={22} />}
            {t(job?.install_action === "upgrade" ? "marketplaceInstall.upgradeTitle" : "marketplaceInstall.title")}
          </DialogTitle>
          <DialogDescription>{job ? `${job.resource_name} · ${job.install_action === "upgrade" ? `v${job.installed_version} → ` : ""}v${job.version}` : t("marketplaceInstall.connecting")}</DialogDescription>
        </DialogHeader>

        {loading && <div role="status" className="flex flex-col items-center gap-3 py-8 text-center text-sm text-muted-foreground"><Loader2 size={24} className="size-6 shrink-0 animate-spin motion-reduce:animate-none" aria-hidden="true" />{t("marketplaceInstall.connecting")}</div>}

        {errorCode && <div className="flex gap-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300"><AlertCircle className="mt-0.5 shrink-0" size={18} /><span>{friendlyError(errorCode)}</span></div>}

        {job && !loading && <>
          <div className="grid grid-cols-2 gap-3 rounded-lg border bg-muted/30 p-4 text-sm">
            <div><span className="block text-muted-foreground">{t("marketplaceInstall.type")}</span><strong>{t(`marketplaceInstall.types.${job.resource_type}`)}</strong></div>
            <div><span className="block text-muted-foreground">{t("marketplaceInstall.statusLabel")}</span><strong>{statusLabel}</strong></div>
          </div>

          {active && <MarketplaceInstallProgress job={job} />}

          {job.status === "ready" && ["upgrade", "downgrade", "replace"].includes(job.install_action || "") && <p className="text-sm text-muted-foreground">{t(`marketplaceInstall.${job.install_action}Hint`, { current: job.installed_version, target: job.version })}</p>}

          {canInstall && <div className="space-y-3"><div className="flex items-center gap-2 text-sm font-medium"><ShieldCheck size={18} className="text-emerald-600" />{t("marketplaceInstall.permissions")}</div>{job.permissions.length ? <div className="flex flex-wrap gap-2">{job.permissions.map((permission) => <span key={permission} className="rounded-md border bg-background px-2.5 py-1 text-xs">{permission}</span>)}</div> : <p className="text-sm text-muted-foreground">{t("marketplaceInstall.noPermissions")}</p>}</div>}

          {canInstall && job.dependencies.length > 0 && <div className="space-y-3"><div className="text-sm font-medium">{t("marketplaceInstall.dependencies")}</div><div className="flex flex-wrap gap-2">{job.dependencies.map((dependency) => <span key={dependency} className="rounded-md border bg-background px-2.5 py-1 text-xs">{dependency}</span>)}</div></div>}

          {canInstall && job.resource_type === "skill" && <p className="text-sm text-muted-foreground">{t("marketplaceInstall.skillActivationHint")}</p>}
          {job.status === "installed" && job.resource_type !== "plugin" && <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-300">{t(`marketplaceInstall.${completionKey}`, { version: job.installed_version || job.version })}</div>}
          {job.status === "installed" && job.resource_type === "plugin" && job.already_installed && !job.installed_pending_restart && <p className="text-sm text-muted-foreground">{t("marketplaceInstall.alreadyInstalled", { version: job.installed_version || job.version })}</p>}
          {open && job.status === "installed" && job.resource_type === "plugin" && <MarketplacePluginSetup
            key={`${apiBaseUrl}/${job.id}`}
            apiBaseUrl={apiBaseUrl} pluginId={job.plugin_id || job.resource_slug || ""}
            onBusyChange={setPluginBusy} onClose={() => void close()}
          />}
          {job.status === "failed" && <div role="alert" className="space-y-2 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
            <p className="font-medium">{t(`marketplaceInstall.failureReasons.${job.failure_reason || "unknown"}`, { defaultValue: friendlyError(job.failure_code || "marketplace_install_failed") })}</p>
            {(job.failure_stage || job.stage) && <p>{t("marketplaceInstall.failureStage", { stage: t(`marketplaceInstall.stages.${job.failure_stage || job.stage}`, { defaultValue: t("marketplaceInstall.status.installing") }) })}</p>}
            {job.current_dependency && <p>{t("marketplaceInstall.lastDependency", { name: job.current_dependency })}</p>}
            {job.failure_detail && <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-background/70 p-2 text-xs font-mono">{job.failure_detail}</pre>}
            <p>{t(job.failure_reason === "dependency_network" ? "marketplaceInstall.networkRetryHint" : "marketplaceInstall.failureRetryHint")}</p>
          </div>}
        </>}

        {!(job?.status === "installed" && job.resource_type === "plugin") && <DialogFooter>
          {!active && <Button variant="outline" disabled={confirming} onClick={() => void close()}>{job?.status === "installed" ? t("common.done", "完成") : t("common.cancel")}</Button>}
          {canInstall && <Button disabled={confirming} onClick={confirm}><Download size={16} />{t(job.install_action === "upgrade" ? "marketplaceInstall.upgrade" : job.install_action === "replace" ? "marketplaceInstall.replace" : "marketplaceInstall.install")}</Button>}
        </DialogFooter>}
      </DialogContent>
    </Dialog>
  );
}
