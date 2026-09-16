import Link from "next/link";
import type { ReactNode } from "react";

const links = [
  { label: "Analyze", href: "/" },
  { label: "Predictions", href: "/predictions" },
  { label: "Performance", href: "/performance" },
] as const;

export function AppShell({ active, children }: { active: string; children: ReactNode }) {
  return (
    <div className="app-shell">
      <header className="site-header">
        <Link className="brand" href="/" aria-label="Model FC home">
          <span className="brand-mark">M</span>
          <span>MODEL FC</span>
          <small>CONTROL ROOM</small>
        </Link>
        <nav aria-label="Primary navigation">
          {links.map((link) => (
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
        <div className="system-status"><span /> Mock data</div>
      </header>
      <main>{children}</main>
    </div>
  );
}
