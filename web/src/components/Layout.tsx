import { useQuery } from "@tanstack/react-query";
import { KeyRound, Monitor, Moon, Plus, ShieldCheck, Sun } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";
import { useLiveState } from "../hooks/useLive";
import { useTheme, type Theme } from "../hooks/useTheme";
import { api } from "../lib/api";
import { cn } from "../lib/cn";

const THEMES: { value: Theme; label: string; Icon: typeof Sun }[] = [
  { value: "system", label: "Match system", Icon: Monitor },
  { value: "light", label: "Light", Icon: Sun },
  { value: "dark", label: "Dark", Icon: Moon },
];

const link = ({ isActive }: { isActive: boolean }) =>
  cn(
    "rounded-lg px-2.5 py-1.5 text-sm font-medium sm:px-3",
    isActive ? "bg-neutralbg" : "text-muted hover:text-fg",
  );

function LiveIndicator() {
  const state = useLiveState();
  const live = state === "live";
  return (
    <span
      className="text-muted flex items-center gap-1.5 text-xs"
      title={live ? "Updates arrive instantly" : "Live updates are paused; refreshing every 15 seconds"}
    >
      <span className={cn("size-2 rounded-full", live ? "bg-ok" : "bg-warn")} aria-hidden />
      <span className="max-sm:sr-only">
        {live ? "Live" : state === "connecting" ? "Connecting" : "Reconnecting"}
      </span>
    </span>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useTheme();
  const next = THEMES[(THEMES.findIndex((t) => t.value === theme) + 1) % THEMES.length]!;
  const current = THEMES.find((t) => t.value === theme)!;
  return (
    <button
      type="button"
      onClick={() => setTheme(next.value)}
      className="text-muted hover:text-fg rounded-lg p-2"
      aria-label={`Theme: ${current.label}. Switch to ${next.label}`}
      title={`Theme: ${current.label}`}
    >
      <current.Icon className="size-4" aria-hidden />
    </button>
  );
}

export function Layout() {
  const ready = useQuery({ queryKey: ["ready"], queryFn: api.ready, retry: false, staleTime: 60_000 });
  const offline = ready.isError;

  return (
    <div className="min-h-screen">
      <a
        href="#main"
        className="bg-brand text-brandfg sr-only rounded-lg px-4 py-2 focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50"
      >
        Skip to content
      </a>
      <header className="bg-surface/90 sticky top-0 z-40 border-b border-line backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center gap-2 px-4 py-3">
          <NavLink to="/" className="mr-2 flex items-center gap-2 font-semibold" end>
            <ShieldCheck className="text-brand size-5" aria-hidden />
            <span className="max-sm:sr-only">IncidentOps</span>
          </NavLink>
          <nav aria-label="Main" className="flex items-center gap-1">
            <NavLink to="/" end className={link}>
              Incidents
            </NavLink>
            <NavLink to="/new" className={link}>
              <span className="inline-flex items-center gap-1">
                <Plus className="size-3.5" aria-hidden /> Report
              </span>
            </NavLink>
          </nav>
          <div className="ml-auto flex items-center gap-1">
            <LiveIndicator />
            <ThemeToggle />
            <NavLink to="/settings" className="text-muted hover:text-fg rounded-lg p-2" aria-label="Settings" title="Settings">
              <KeyRound className="size-4" aria-hidden />
            </NavLink>
          </div>
        </div>
      </header>
      {offline && (
        <div role="alert" className="bg-dangerbg text-danger px-4 py-2 text-center text-sm">
          Can't reach the IncidentOps server. Check that it is running, then reload.
        </div>
      )}
      <main id="main" className="mx-auto max-w-5xl px-4 py-6 sm:py-8">
        <Outlet />
      </main>
    </div>
  );
}
