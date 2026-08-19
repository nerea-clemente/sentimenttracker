"use client";

import { IS_STATIC, snapshotInfo } from "@/lib/api";

/**
 * Says, on every page, that this is a snapshot and when it was taken.
 *
 * A read-only build that looks like the live tool is how someone quotes a three-week-old figure
 * in a meeting. The date is the point; the styling is deliberately hard to skim past.
 */
export function SnapshotBanner() {
  if (!IS_STATIC) return null;
  const info = snapshotInfo();
  if (!info) return null;

  return (
    <div className="banner warn" style={{ marginBottom: "1.25rem" }}>
      <strong>Read-only snapshot — generated {info.generated_at}</strong>
      <div className="small">
        Every figure here was computed by the metrics engine at that moment and baked into this
        page. Nothing refreshes on load, and nothing can be logged or edited. For live data, and to
        record escalations or enable watch rules, run the tool locally
        (<code className="inline">make api</code> and <code className="inline">make web</code>).
      </div>
      {info.warnings.length > 0 && (
        <ul>
          {info.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
