"use client";

import { useState } from "react";
import { EvidenceDrawer } from "./EvidenceDrawer";
import { formatMetricValue, type Metric } from "@/lib/api";

/**
 * One cell in a measured table.
 *
 * A cell is either a number that opens its own source rows, or a stated reason it is unavailable.
 * It is never a bare zero standing in for "we do not know".
 */
export function MetricCell({
  metric,
  campaignLabel,
}: {
  metric: Metric | undefined;
  campaignLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  if (!metric) return <span className="muted">—</span>;

  if (!metric.available) {
    return (
      <span className="unavailable" title={metric.unavailable_reason}>
        not available
        <span className="small muted" style={{ display: "block", fontStyle: "normal" }}>
          {metric.unavailable_reason}
        </span>
      </span>
    );
  }

  const hasRows = metric.row_count > 0;
  return (
    <>
      <button
        className={`drill${hasRows ? "" : " empty"}`}
        onClick={() => hasRows && setOpen(true)}
        disabled={!hasRows}
        title={
          hasRows
            ? `${metric.row_count} source row(s) — click to see them`
            : "No source rows behind this value"
        }
      >
        {formatMetricValue(metric)}
      </button>
      {metric.caveats.filter(Boolean).length > 0 && (
        <span
          className="pill"
          style={{ marginLeft: "0.4rem" }}
          title={metric.caveats.filter(Boolean).join("\n\n")}
        >
          caveat
        </span>
      )}
      {open && (
        <EvidenceDrawer
          metric={metric}
          campaignLabel={campaignLabel}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}
