import Link from "next/link";
import { AppShell } from "@/components/app-shell";
import { BttsResearchDashboard } from "@/components/btts-research-dashboard";

export default function BttsResearchPage() {
  return <AppShell active="BTTS Value"><BttsResearchDashboard /><p><Link href="/research/btts/performance">Championship BTTS outcomes &amp; performance</Link></p></AppShell>;
}
