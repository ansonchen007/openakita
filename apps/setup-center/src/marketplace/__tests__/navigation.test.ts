import { afterEach, describe, expect, it, vi } from "vitest";
import {
  buildMarketplaceContextUrl,
  buildMarketplaceContextUrlFromDeepLink,
  hasMarketplaceClientVersion,
  marketplaceDeepLinkAction,
  normalizeMarketplaceClientVersion,
} from "../navigation";

describe("Marketplace navigation", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("normalizes the desktop version to the Marketplace contract", () => {
    expect(normalizeMarketplaceClientVersion("v01.027.004-beta.1")).toBe("1.27.4");
    expect(normalizeMarketplaceClientVersion("1.27")).toBeNull();
    expect(hasMarketplaceClientVersion("0.0.0")).toBe(false);
    expect(hasMarketplaceClientVersion("1.27.40")).toBe(true);
  });

  it("opens the Marketplace home through the client context endpoint", () => {
    vi.stubEnv("VITE_MARKETPLACE_URL", "");
    const result = new URL(buildMarketplaceContextUrl("1.27.40"));
    expect(result.origin).toBe("https://marketplace.openakita.cn");
    expect(result.pathname).toBe("/openakita/context");
    expect(result.searchParams.get("version")).toBe("1.27.40");
    expect(result.searchParams.get("next")).toBe("/");
  });

  it("restores a clean Marketplace detail URL from an open deep link", () => {
    const deepLink = "openakita://marketplace/open?return_url="
      + encodeURIComponent("https://marketplace.openakita.cn/resources/example?tab=versions#versions");
    const result = new URL(buildMarketplaceContextUrlFromDeepLink(deepLink, "1.27.40")!);

    expect(result.origin).toBe("https://marketplace.openakita.cn");
    expect(result.pathname).toBe("/openakita/context");
    expect(result.searchParams.get("version")).toBe("1.27.40");
    expect(result.searchParams.get("next")).toBe("/resources/example?tab=versions#versions");
  });

  it("allows a configured Marketplace origin and local development origins", () => {
    const configured = "https://market.example.com";
    const configuredLink = "openakita://marketplace/open?return_url="
      + encodeURIComponent("https://market.example.com/catalog");
    const localLink = "openakita://marketplace/open?return_url="
      + encodeURIComponent("http://localhost:3001/catalog");

    expect(buildMarketplaceContextUrlFromDeepLink(configuredLink, "1.27.40", configured))
      .toContain("https://market.example.com/openakita/context?");
    expect(buildMarketplaceContextUrlFromDeepLink(localLink, "1.27.40", configured))
      .toContain("http://localhost:3001/openakita/context?");
  });

  it("rejects untrusted return URLs and unsupported actions", () => {
    const malicious = "openakita://marketplace/open?return_url="
      + encodeURIComponent("https://evil.example/resources/example");

    expect(buildMarketplaceContextUrlFromDeepLink(malicious, "1.27.40")).toBeNull();
    expect(marketplaceDeepLinkAction("openakita://marketplace/open?return_url=x")).toBe("open");
    expect(marketplaceDeepLinkAction("openakita://marketplace/install?token=x")).toBe("install");
    expect(marketplaceDeepLinkAction("openakita://marketplace/unknown")).toBeNull();
  });

  it("prevents recursive client context targets", () => {
    const result = new URL(buildMarketplaceContextUrl(
      "1.27.40",
      "/openakita/context?version=0.0.0&next=/catalog",
    ));
    expect(result.searchParams.get("next")).toBe("/");
  });
});
