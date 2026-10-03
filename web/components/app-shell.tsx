import Link from "next/link";
import type { ReactNode } from "react";
import { apiMode } from "@/lib/api/client";

const links = [
  { label: "Analyze", href: "/analyze" },
  { label: "Predictions", href: "/predictions" },
  { label: "Teams", href: "/teams" },
  { label: "Performance", href: "/performance" },
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
          {links.filter((link) => apiMode !== "live" || link.href !== "/analyze").map((link) => (
            <Link
              aria-current={active === link.label ? "page" : undefined}
              className={active === link.label ? "active" : ""}
              href={link.href}
              key={link.href}
            >
              {link.label}
            </Link>
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
