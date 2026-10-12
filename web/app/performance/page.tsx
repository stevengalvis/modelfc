import Link from "next/link";
import { AppShell } from "@/components/app-shell";
import { PerformanceDashboard } from "@/components/prospective-dashboard";

export default function PerformancePage() {
  return <AppShell active="Performance"><PerformanceDashboard /><p><Link href="/research/btts/performance">Championship BTTS outcomes &amp; performance</Link></p></AppShell>;
}
