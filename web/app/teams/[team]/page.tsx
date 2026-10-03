import { AppShell } from "@/components/app-shell";
import { TeamProfileDashboard } from "@/components/team-intelligence";
export default async function TeamPage({ params }: { params: Promise<{ team: string }> }) {
  const { team } = await params;
  return <AppShell active="Teams"><TeamProfileDashboard teamId={team} /></AppShell>;
}
