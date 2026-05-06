import type { ThinkingEffort } from "./api";

export const DEFAULT_ZENMUX_BASE_URL = "https://zenmux.ai/api/v1";
const STORAGE_KEY = "osz.runtime_config.v1";

export type RuntimeConfig = {
  zenmuxApiKey: string;
  zenmuxBaseUrl: string;
  modelOverrides: Record<string, string>;
  thinkingEffortOverrides: Record<string, ThinkingEffort>;
};

export const DEFAULT_RUNTIME_CONFIG: RuntimeConfig = {
  zenmuxApiKey: "",
  zenmuxBaseUrl: DEFAULT_ZENMUX_BASE_URL,
  modelOverrides: {},
  thinkingEffortOverrides: {},
};

function storage(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.sessionStorage : null;
  } catch {
    return null;
  }
}

export function readRuntimeConfig(): RuntimeConfig {
  const store = storage();
  if (!store) return DEFAULT_RUNTIME_CONFIG;
  try {
    const raw = store.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_RUNTIME_CONFIG;
    const parsed = JSON.parse(raw) as Partial<RuntimeConfig>;
    return {
      zenmuxApiKey: typeof parsed.zenmuxApiKey === "string" ? parsed.zenmuxApiKey : "",
      zenmuxBaseUrl:
        typeof parsed.zenmuxBaseUrl === "string" && parsed.zenmuxBaseUrl.trim()
          ? parsed.zenmuxBaseUrl.trim()
          : DEFAULT_ZENMUX_BASE_URL,
      modelOverrides:
        parsed.modelOverrides && typeof parsed.modelOverrides === "object"
          ? Object.fromEntries(
              Object.entries(parsed.modelOverrides).filter((entry): entry is [string, string] =>
                typeof entry[1] === "string" && entry[1].trim().length > 0,
              ),
            )
          : {},
      thinkingEffortOverrides:
        parsed.thinkingEffortOverrides && typeof parsed.thinkingEffortOverrides === "object"
          ? Object.fromEntries(
              Object.entries(parsed.thinkingEffortOverrides).filter((entry): entry is [string, ThinkingEffort] =>
                typeof entry[1] === "string" && entry[1].trim().length > 0,
              ),
            )
          : {},
    };
  } catch {
    return DEFAULT_RUNTIME_CONFIG;
  }
}

export function writeRuntimeConfig(config: RuntimeConfig): void {
  const store = storage();
  if (!store) return;
  store.setItem(
    STORAGE_KEY,
    JSON.stringify({
      zenmuxApiKey: config.zenmuxApiKey,
      zenmuxBaseUrl: config.zenmuxBaseUrl || DEFAULT_ZENMUX_BASE_URL,
      modelOverrides: compactRecord(config.modelOverrides),
      thinkingEffortOverrides: compactRecord(config.thinkingEffortOverrides),
    }),
  );
}

export function runtimeConfigHeaders(): Record<string, string> {
  const config = readRuntimeConfig();
  const headers: Record<string, string> = {};
  if (config.zenmuxApiKey.trim()) {
    headers["X-OSZ-Zenmux-Key"] = config.zenmuxApiKey.trim();
  }
  if (config.zenmuxBaseUrl.trim()) {
    headers["X-OSZ-Zenmux-Base-Url"] = config.zenmuxBaseUrl.trim();
  }
  const modelOverrides = compactRecord(config.modelOverrides);
  if (Object.keys(modelOverrides).length > 0) {
    headers["X-OSZ-Model-Overrides"] = JSON.stringify(modelOverrides);
  }
  const effortOverrides = compactRecord(config.thinkingEffortOverrides);
  if (Object.keys(effortOverrides).length > 0) {
    headers["X-OSZ-Thinking-Effort-Overrides"] = JSON.stringify(effortOverrides);
  }
  return headers;
}

export function compactRecord<T extends string>(record: Record<string, T>): Record<string, T> {
  return Object.fromEntries(
    Object.entries(record).filter(([, value]) => typeof value === "string" && value.trim().length > 0),
  ) as Record<string, T>;
}

export function hasRuntimeZenmuxKey(config: RuntimeConfig = readRuntimeConfig()): boolean {
  return config.zenmuxApiKey.trim().length > 0;
}
