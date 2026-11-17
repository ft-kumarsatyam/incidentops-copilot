import { CheckCircle2, Loader2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { useToast } from "../hooks/useToast";
import { api } from "../lib/api";
import { saveSettings, useSettings } from "../lib/settings";

const input = "bg-surface w-full rounded-lg border border-line px-3 py-2 text-sm";

export function SettingsPage() {
  const settings = useSettings();
  const toast = useToast();
  const [testing, setTesting] = useState(false);
  const [verified, setVerified] = useState(false);
  const [serverAi, setServerAi] = useState<{ topology?: string; mcp_enabled?: boolean } | null>(null);

  useEffect(() => {
    void fetch("/ready")
      .then((response) => response.json())
      .then((body: { ai?: { topology?: string; mcp_enabled?: boolean } }) => {
        setServerAi(body.ai ?? null);
      })
      .catch(() => setServerAi(null));
  }, []);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    saveSettings({
      apiKey: String(form.get("apiKey") ?? "").trim(),
      actor: String(form.get("actor") ?? "").trim(),
      remember: form.get("remember") === "on",
    });
    setVerified(false);
    toast("success", "Settings saved.");
  };

  const test = async () => {
    setTesting(true);
    try {
      await api.list(1);
      setVerified(true);
      toast("success", "Connected. Your key works.");
    } catch (failure) {
      setVerified(false);
      toast("error", failure instanceof Error ? failure.message : "Connection failed.");
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="mx-auto max-w-xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Settings</h1>
        <p className="text-muted mt-1 text-sm">Stored in this browser only. They are never sent anywhere except your IncidentOps server.</p>
      </div>
      {serverAi && (
        <div className="bg-surface2 rounded-lg border border-line p-4 text-sm">
          <p className="font-medium">Server AI configuration</p>
          <p className="text-muted mt-1">
            Topology: <span className="text-fg font-mono">{serverAi.topology ?? "unknown"}</span>
            {" · "}
            MCP read bridge: <span className="text-fg font-mono">{serverAi.mcp_enabled ? "on" : "off"}</span>
          </p>
        </div>
      )}
      <form onSubmit={submit} className="space-y-5">
        <div>
          <label htmlFor="actor" className="mb-1 block text-sm font-medium">
            Your name
          </label>
          <input id="actor" name="actor" defaultValue={settings.actor} className={input} autoComplete="name" />
          <p className="text-muted mt-1 text-xs">Recorded in the activity log next to your decisions.</p>
        </div>
        <div>
          <label htmlFor="apiKey" className="mb-1 block text-sm font-medium">
            API key
          </label>
          <input
            id="apiKey"
            name="apiKey"
            type="password"
            defaultValue={settings.apiKey}
            className={input}
            autoComplete="off"
            placeholder="Leave empty for a local development server"
          />
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" name="remember" defaultChecked={settings.remember} className="accent-brand size-4" />
          Remember on this device (otherwise it is forgotten when you close the tab)
        </label>
        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" className="bg-brand text-brandfg hover:bg-brandhover rounded-lg px-4 py-2 text-sm font-semibold">
            Save
          </button>
          <button
            type="button"
            onClick={() => void test()}
            disabled={testing}
            className="hover:bg-surface2 inline-flex items-center gap-2 rounded-lg border border-line px-4 py-2 text-sm font-semibold disabled:opacity-60"
          >
            {testing && <Loader2 className="size-4 animate-spin" aria-hidden />}
            Test connection
          </button>
          {verified && (
            <span className="text-ok inline-flex items-center gap-1 text-sm">
              <CheckCircle2 className="size-4" aria-hidden /> Connected
            </span>
          )}
        </div>
      </form>
    </div>
  );
}
