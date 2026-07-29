export type SessionRestoreEvent =
  | { type: "attempt"; attempt: number }
  | { type: "not_ready"; attempt: number; delayMs: number }
  | { type: "error"; attempt: number; delayMs: number; error: unknown }
  | { type: "restored"; attempt: number }
  | { type: "cancelled"; attempt: number };

export type SessionRestoreResult = {
  status: "restored" | "cancelled";
  attempts: number;
};

export function sessionRestoreRetryDelay(attempt: number): number {
  return Math.min(1000 * Math.pow(1.5, Math.max(0, attempt - 1)), 5000);
}

function waitForRetry(delayMs: number, signal: AbortSignal): Promise<boolean> {
  if (signal.aborted) return Promise.resolve(false);

  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve(true);
    }, delayMs);
    const onAbort = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
      resolve(false);
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

export async function restoreSessionsUntilReady<T>(options: {
  signal: AbortSignal;
  request: (attempt: number) => Promise<T>;
  isReady: (payload: T) => boolean;
  reconcile: (payload: T) => void | Promise<void>;
  onEvent?: (event: SessionRestoreEvent) => void;
  retryDelay?: (attempt: number) => number;
  wait?: (delayMs: number, signal: AbortSignal) => Promise<boolean>;
}): Promise<SessionRestoreResult> {
  const retryDelay = options.retryDelay ?? sessionRestoreRetryDelay;
  const wait = options.wait ?? waitForRetry;
  let attempt = 0;

  while (!options.signal.aborted) {
    attempt += 1;
    options.onEvent?.({ type: "attempt", attempt });

    try {
      const payload = await options.request(attempt);
      if (options.signal.aborted) break;

      if (options.isReady(payload)) {
        await options.reconcile(payload);
        if (options.signal.aborted) break;
        options.onEvent?.({ type: "restored", attempt });
        return { status: "restored", attempts: attempt };
      }

      const delayMs = retryDelay(attempt);
      options.onEvent?.({ type: "not_ready", attempt, delayMs });
      if (!(await wait(delayMs, options.signal))) break;
    } catch (error) {
      if (options.signal.aborted) break;
      const delayMs = retryDelay(attempt);
      options.onEvent?.({ type: "error", attempt, delayMs, error });
      if (!(await wait(delayMs, options.signal))) break;
    }
  }

  options.onEvent?.({ type: "cancelled", attempt });
  return { status: "cancelled", attempts: attempt };
}
