"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { section: "Measure" },
  { href: "/", label: "Campaign comparison" },
  { href: "/campaigns", label: "Campaigns" },
  { section: "Log" },
  { href: "/escalations", label: "Escalation log" },
  { href: "/watchlist", label: "Watchlist & triggers" },
  { section: "Out" },
  { href: "/export", label: "Export" },
  { href: "/definitions", label: "Metric definitions" },
  { href: "/data-quality", label: "Data quality" },
];

export function SideNav() {
  const pathname = usePathname();
  return (
    <nav className="sidebar">
      <div className="brand">
        Campaign Impact Benchmarker
        <small>Measured footprints, not ratings</small>
      </div>
      {LINKS.map((link, i) =>
        "section" in link ? (
          <div className="section" key={`s${i}`}>
            {link.section}
          </div>
        ) : (
          <Link
            key={link.href}
            href={link.href!}
            className={
              pathname === link.href ||
              (link.href !== "/" && pathname.startsWith(link.href!))
                ? "active"
                : ""
            }
          >
            {link.label}
          </Link>
        ),
      )}
    </nav>
  );
}
