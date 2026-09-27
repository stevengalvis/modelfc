import { AnalyzeWorkspace } from "@/components/analyze-workspace";
import { AppShell } from "@/components/app-shell";

export default function AnalyzePage() {
  return (
    <AppShell active="Analyze">
      <AnalyzeWorkspace />
    </AppShell>
  );
}
