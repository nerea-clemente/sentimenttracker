"use client";

import { useEffect, useRef, useState } from "react";
import { api, formatDate, type Metric } from "@/lib/api";

/**
 * The drill-down behind every cell.
 *
 * It renders whatever `row_ids` a metric carries by resolving them through one generic endpoint,
 * so a new metric is drillable the moment it exists — there is no per-metric drill-down code to
 * forget to write.
 */
export function EvidenceDrawer({
  metric,
  campaignLabel,
  onClose,
}: {
  metric: Metric;
  campaignLabel?: string;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [rows, setRows] = useState<any[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    ref.current?.showModal();
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (!metric.row_ids.length || !metric.row_table) {
      setRows([]);
      return;
    }
    api
      .evidence(metric.row_table, metric.row_ids)
      .then((r) => !cancelled && setRows(r.rows))
      .catch((e) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [metric]);

  return (
    <dialog className="drawer" ref={ref} onClose={onClose}>
      <header>
        <div>
          <strong>{metric.label}</strong>
          {campaignLabel && <span className="muted"> — {campaignLabel}</span>}
          <div className="small muted" style={{ marginTop: "0.3rem", maxWidth: "52rem" }}>
            {metric.definition.plain}
          </div>
          <div className="small muted" style={{ marginTop: "0.2rem" }}>
            <strong style={{ display: "inline" }}>Counts:</strong>{" "}
            {metric.definition.counts}
          </div>
        </div>
        <button onClick={() => ref.current?.close()}>Close</button>
      </header>
      <div className="body">
        {metric.caveats.filter(Boolean).length > 0 && (
          <div className="banner warn">
            <strong>Caveats</strong>
            <ul>
              {metric.caveats.filter(Boolean).map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          </div>
        )}

        <p className="small muted">
          {metric.row_count} source row(s) in <code className="inline">{metric.row_table || "—"}</code>.
          Computed {metric.computed_at}.
        </p>

        {error && <div className="banner danger">{error}</div>}
        {rows === null && !error && <p className="muted">Loading source rows…</p>}
        {rows?.length === 0 && (
          <div className="empty-state">
            <strong>No source rows.</strong>
            This value is not derived from imported records — check the metric&apos;s definition
            above before quoting it.
          </div>
        )}

        {rows && rows.length > 0 && metric.row_table === "articles" && (
          <ArticleRows rows={rows} />
        )}
        {rows && rows.length > 0 && metric.row_table === "mentions" && (
          <MentionRows rows={rows} />
        )}
        {rows && rows.length > 0 && metric.row_table === "escalations" && (
          <EscalationRows rows={rows} />
        )}
        {rows &&
          rows.length > 0 &&
          !["articles", "mentions", "escalations"].includes(metric.row_table) && (
            <GenericRows rows={rows} />
          )}
      </div>
    </dialog>
  );
}

function ArticleRows({ rows }: { rows: any[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th className="num">Day</th>
            <th>Date</th>
            <th>Outlet</th>
            <th>Tier</th>
            <th>Headline</th>
            <th>Original</th>
            <th>Imported from</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td className="num">{r.day_index ?? "—"}</td>
              <td className="nowrap">{formatDate(r.published_at)}</td>
              <td>
                {r.outlet}
                {r.country ? <span className="muted small"> {r.country}</span> : null}
              </td>
              <td className="small muted">{r.tier}</td>
              <td>
                {r.url ? (
                  <a href={r.url} target="_blank" rel="noreferrer">
                    {r.headline}
                  </a>
                ) : (
                  r.headline
                )}
              </td>
              <td className="small">
                {r.is_original ? "yes" : <span className="muted">syndicated</span>}
              </td>
              <td className="small muted nowrap">
                {r.import_source}
                {r.file_name ? ` · ${r.file_name}` : ""}
                <br />
                {formatDate(r.imported_at)} by {r.imported_by}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MentionRows({ rows }: { rows: any[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th className="num">Day</th>
            <th>Entity</th>
            <th>Role</th>
            <th>Evidence sentence</th>
            <th>Classified by</th>
            <th>Article</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td className="num">{r.day_index ?? "—"}</td>
              <td>{r.entity_name}</td>
              <td className="small">{r.role}</td>
              <td style={{ maxWidth: "28rem" }}>{r.evidence_sentence}</td>
              <td className="small muted">
                {r.classified_by}
                {r.confidence != null && ` (${r.confidence})`}
              </td>
              <td className="small">
                {r.url ? (
                  <a href={r.url} target="_blank" rel="noreferrer">
                    {r.outlet}
                  </a>
                ) : (
                  r.outlet
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EscalationRows({ rows }: { rows: any[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Date</th>
            <th>Type</th>
            <th>Actor</th>
            <th className="num">Severity</th>
            <th>Description</th>
            <th>Source</th>
            <th>Verified</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td className="nowrap">{formatDate(r.occurred_at)}</td>
              <td className="small">{r.escalation_type}</td>
              <td>{r.actor_name}</td>
              <td className="num">{r.severity}</td>
              <td style={{ maxWidth: "24rem" }}>{r.description}</td>
              <td className="small">
                <a href={r.source_url} target="_blank" rel="noreferrer">
                  link
                </a>
              </td>
              <td className="small muted">{r.verified_by ?? "unverified"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GenericRows({ rows }: { rows: any[] }) {
  const columns = Object.keys(rows[0]);
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c} className="small">
                  {r[c] === null ? <span className="muted">—</span> : String(r[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
