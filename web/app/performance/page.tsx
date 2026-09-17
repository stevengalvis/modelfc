import { AppShell } from "@/components/app-shell";

export default function PerformancePage() {
  return (
    <AppShell active="Performance">
      <section className="empty-state">
        <p className="eyebrow">Running record</p>
        <h1>Performance arrives after logging.</h1>
        <p>This route will use backend-provided aggregates only.</p>
      </section>
    </AppShell>
  );
}
