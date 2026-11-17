import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { LiveProvider } from "./hooks/useLive";
import { ToastProvider } from "./hooks/useToast";
import { ApiError } from "./lib/api";
import { IncidentPage } from "./pages/IncidentPage";
import { IncidentsPage } from "./pages/IncidentsPage";
import { NewIncidentPage } from "./pages/NewIncidentPage";
import { SettingsPage } from "./pages/SettingsPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5_000,
      // Auth and validation errors will not fix themselves; do not hammer the server.
      retry: (count, error) => !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 2,
    },
  },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <LiveProvider>
          {/* Hash routing keeps UI routes from colliding with the API's /incidents paths. */}
          <HashRouter>
            <Routes>
              <Route element={<Layout />}>
                <Route index element={<IncidentsPage />} />
                <Route path="incidents/:runId" element={<IncidentPage />} />
                <Route path="new" element={<NewIncidentPage />} />
                <Route path="settings" element={<SettingsPage />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Route>
            </Routes>
          </HashRouter>
        </LiveProvider>
      </ToastProvider>
    </QueryClientProvider>
  );
}
