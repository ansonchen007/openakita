import { describe, expect, it, vi } from "vitest";

import {
  restoreSessionsUntilReady,
  sessionRestoreRetryDelay,
  type SessionRestoreEvent,
} from "../sessionRestore";

describe("restoreSessionsUntilReady", () => {
  it("continues past six attempts until the session manager is ready", async () => {
    const controller = new AbortController();
    const events: SessionRestoreEvent[] = [];
    const request = vi.fn(async () => ({ ready: request.mock.calls.length >= 7 }));
    const reconcile = vi.fn();

    const result = await restoreSessionsUntilReady({
      signal: controller.signal,
      request,
      isReady: (payload) => payload.ready,
      reconcile,
      onEvent: (event) => events.push(event),
      retryDelay: () => 0,
      wait: async () => true,
    });

    expect(result).toEqual({ status: "restored", attempts: 7 });
    expect(request).toHaveBeenCalledTimes(7);
    expect(reconcile).toHaveBeenCalledTimes(1);
    expect(events.at(-1)).toEqual({ type: "restored", attempt: 7 });
  });

  it("retries request failures and only reconciles a ready response", async () => {
    const controller = new AbortController();
    const request = vi
      .fn<() => Promise<{ ready: boolean }>>()
      .mockRejectedValueOnce(new Error("connection refused"))
      .mockResolvedValueOnce({ ready: false })
      .mockResolvedValueOnce({ ready: true });
    const reconcile = vi.fn();

    const result = await restoreSessionsUntilReady({
      signal: controller.signal,
      request,
      isReady: (payload) => payload.ready,
      reconcile,
      retryDelay: () => 0,
      wait: async () => true,
    });

    expect(result).toEqual({ status: "restored", attempts: 3 });
    expect(reconcile).toHaveBeenCalledWith({ ready: true });
  });

  it("does not complete until reconciliation succeeds", async () => {
    const controller = new AbortController();
    const reconcile = vi
      .fn<(payload: { ready: boolean }) => Promise<void>>()
      .mockRejectedValueOnce(new Error("local storage unavailable"))
      .mockResolvedValueOnce();

    const result = await restoreSessionsUntilReady({
      signal: controller.signal,
      request: async () => ({ ready: true }),
      isReady: (payload) => payload.ready,
      reconcile,
      retryDelay: () => 0,
      wait: async () => true,
    });

    expect(result).toEqual({ status: "restored", attempts: 2 });
    expect(reconcile).toHaveBeenCalledTimes(2);
  });

  it("stops retrying when cancelled", async () => {
    const controller = new AbortController();
    const events: SessionRestoreEvent[] = [];

    const result = await restoreSessionsUntilReady({
      signal: controller.signal,
      request: async () => ({ ready: false }),
      isReady: (payload) => payload.ready,
      reconcile: vi.fn(),
      onEvent: (event) => events.push(event),
      wait: async () => {
        controller.abort();
        return false;
      },
    });

    expect(result).toEqual({ status: "cancelled", attempts: 1 });
    expect(events.at(-1)).toEqual({ type: "cancelled", attempt: 1 });
  });
});

describe("sessionRestoreRetryDelay", () => {
  it("caps retries at five seconds", () => {
    expect(sessionRestoreRetryDelay(1)).toBe(1000);
    expect(sessionRestoreRetryDelay(2)).toBe(1500);
    expect(sessionRestoreRetryDelay(10)).toBe(5000);
  });
});
