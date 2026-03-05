import type { CapacitorConfig } from "@capacitor/cli";

const config: CapacitorConfig = {
  appId: "com.openakita.mobile",
  appName: "OpenAkita",
  webDir: "dist-web",
  server: {
    androidScheme: "http",
    iosScheme: "capacitor",
    allowNavigation: ["*"],
  },
  android: {
    allowMixedContent: true,
  },
};

export default config;
