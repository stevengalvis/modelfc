import { AppShell } from "@/components/app-shell";
import { OpportunityDetailView } from "@/components/opportunity-detail";

export default async function OpportunityPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <AppShell active="Predictions"><OpportunityDetailView id={id} /></AppShell>;
}
