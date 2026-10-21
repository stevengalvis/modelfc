import Link from "next/link";
import type { ReactNode } from "react";
import { apiMode } from "@/lib/api/client";

const groups = [
  { label: "Upcoming", links: [
    { label: "Next matches", href: "/upcoming" },
    { label: "Recommendations", href: "/recommendations" },
    { label: "Predictions", href: "/predictions" },
  ] },
  { label: "Performance", links: [
    { label: "Performance", href: "/performance" },
  ] },
  { label: "Insights", links: [
    { label: "Teams", href: "/teams" },
    { label: "BTTS Value", href: "/research/btts" },
    { label: "Analyze", href: "/analyze" },
  ] },
] as const;

export function AppShell({ active, children }: { active: string; children: ReactNode }) {
  return (
    <div className="app-shell">
      <header className="site-header">
        <Link className="brand" href="/" aria-label="Zeno FC home">
          <span className="brand-mark">Z</span>
          <span>ZENO FC</span>
          <small>CONTROL ROOM</small>
        </Link>
        <nav aria-label="Primary navigation">
          {groups.map((group) => (
            <div className="nav-group" role="group" aria-label={group.label} key={group.label}>
              <span className="nav-group-label" aria-hidden="true">{group.label}</span>
              <div className="nav-group-links">
                {group.links.filter((link) => apiMode !== "live" || link.href !== "/analyze").map((link) => (
                  <Link
                    aria-current={active === link.label ? "page" : undefined}
                    className={active === link.label ? "active" : ""}
                    href={link.href}
                    key={link.href}
                  >
                    {link.label}
                  </Link>
                ))}
              </div>
            </div>
          ))}
        </nav>
        <div className={`system-status ${apiMode || "unconfigured"}`}><span />
          {apiMode === "mock" ? "Mock data" : apiMode === "live" ? "Live API" : "API mode not configured"}
        </div>
      </header>
      <main>{children}</main>
    </div>
  );
}
