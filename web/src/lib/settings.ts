import { useSyncExternalStore } from "react";

export interface Settings {
  apiKey: string;
  actor: string;
  remember: boolean;
}

const KEY = "incidentops.settings";
const listeners = new Set<() => void>();
let cached: Settings | null = null;

function read(): Settings {
  const empty: Settings = { apiKey: "", actor: "", remember: false };
  for (const store of safeStores()) {
    try {
      const raw = store.getItem(KEY);
      if (raw) return { ...empty, ...(JSON.parse(raw) as Partial<Settings>) };
    } catch {
      /* storage blocked or corrupt: fall through to defaults */
    }
  }
  return empty;
}

function safeStores(): Storage[] {
  const stores: Storage[] = [];
  try {
    stores.push(window.sessionStorage, window.localStorage);
  } catch {
    /* private mode */
  }
  return stores;
}

export function getSettings(): Settings {
  cached ??= read();
  return cached;
}

export function saveSettings(next: Settings): void {
  cached = next;
  for (const store of safeStores()) {
    try {
      store.removeItem(KEY);
    } catch {
      /* ignore */
    }
  }
  try {
    // The key stays in sessionStorage unless the user explicitly opts in to remembering it.
    (next.remember ? window.localStorage : window.sessionStorage).setItem(KEY, JSON.stringify(next));
  } catch {
    /* keep the in-memory value for this tab */
  }
  listeners.forEach((listener) => listener());
}

export function useSettings(): Settings {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    getSettings,
    getSettings,
  );
}
