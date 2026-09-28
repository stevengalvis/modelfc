import { AppShell } from "@/components/app-shell";

export default function PredictionsPage() {
  return (
    <AppShell active="Predictions">
      <section className="empty-state">
        <p className="eyebrow">Prediction ledger</p>
        <h1>Logged picks will live here.</h1>
        <p>This route is reserved for the next vertical slice.</p>
      </section>
    </AppShell>
  );
}
