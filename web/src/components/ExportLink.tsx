"use client";

import { IS_STATIC } from "@/lib/api";

/**
 * A download link that knows when it cannot exist.
 *
 * `api.exportUrls.*` returns null for exports that only the live tool can generate — a comparison
 * CSV depends on which campaigns the reader picked, so it cannot be baked into a static build.
 * Rendering a dead link would be worse than rendering none, so this renders the reason instead.
 */
export function ExportLink({
  href,
  children,
  primary,
  unavailableHint,
}: {
  href: string | null;
  children: React.ReactNode;
  primary?: boolean;
  unavailableHint?: string;
}) {
  if (href) {
    return (
      <a
        className={`btn${primary ? " primary" : ""}`}
        href={href}
        target="_blank"
        rel="noreferrer"
      >
        {children}
      </a>
    );
  }
  return (
    <span
      className="btn"
      style={{ opacity: 0.55, cursor: "not-allowed" }}
      title={
        unavailableHint ??
        (IS_STATIC
          ? "Generated on demand by the tool. This is a read-only snapshot, so it is not available here — run `make api` locally."
          : "Not available.")
      }
      aria-disabled="true"
    >
      {children} <span className="small">(needs the live tool)</span>
    </span>
  );
}
