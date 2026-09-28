import { AnalyzeWorkspace } from "@/components/analyze-workspace";
import { AppShell } from "@/components/app-shell";
import { apiMode } from "@/lib/api/client";

export default function AnalyzePage() {
  return (
    <AppShell active="Analyze">
      {apiMode === "live" ? (
        <section><h1>Analyze unavailable</h1><p>Interactive analysis is unavailable on this public read-only site.</p></section>
      ) : <AnalyzeWorkspace />}
    </AppShell>
  );
}
