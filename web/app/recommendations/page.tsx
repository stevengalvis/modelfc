import { AppShell } from "@/components/app-shell";
import { RecommendationsDashboard } from "@/components/recommendations-dashboard";

export default function RecommendationsPage() {
  return <AppShell active="Recommendations"><RecommendationsDashboard /></AppShell>;
}
