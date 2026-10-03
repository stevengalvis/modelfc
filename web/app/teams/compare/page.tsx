import { AppShell } from "@/components/app-shell";
import { TeamComparison } from "@/components/team-comparison";

export default async function ComparePage({
  searchParams,
}: {
  searchParams: Promise<{ team?: string | string[] }>;
}) {
  const { team } = await searchParams;
  return (
    <AppShell active="Teams">
      <TeamComparison
        key={typeof team === "string" ? team : ""}
        initialTeam={typeof team === "string" ? team : ""}
      />
    </AppShell>
  );
}
