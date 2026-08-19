"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, formatDate, type CampaignSummary } from "@/lib/api";
import { ErrorBanner, Loading } from "@/components/Loading";

/**
 * Data quality across the whole database.
 *
 * Where every dataset came from, when it was last imported, and how much is missing. The share of
 * outlets without a sourced reach figure is the number that decides whether reach can be quoted
 * at all.
 */
export default function DataQualityPage() {
  const [campaigns, setCampaigns] = useState<CampaignSummary[] | null>(null);
  const [imports, setImports] = useState<any[]>([]);
  const [outlets, setOutlets] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.campaigns(), api.imports(), api.allOutlets()])
      .then(([c, i, o]) => {
        setCampaigns(c);
        setImports(i);
        setOutlets(o);
      })
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <ErrorBanner error={error} />;
  if (!campaigns || !outlets) return <Loading what="data quality" />;

  return (
    <>
      <h1>Data quality</h1>
      <p className="lede">
        Which figures can be quoted and which cannot. Every gap here is a limit on what the
        comparison view is entitled to claim.
      </p>

      <h2>Reach coverage</h2>
      <div
        className={
          outlets.reach_coverage_pct === null || outlets.reach_coverage_pct < 50
            ? "banner warn"
            : "banner info"
        }
      >
        <strong>
          {outlets.with_sourced_reach} of {outlets.count} outlets have a sourced reach figure
          {outlets.reach_coverage_pct !== null && ` (${outlets.reach_coverage_pct}%)`}
        </strong>
        Reach totals are a floor, not a total, until this reaches 100%. A reach figure can only be
        stored with a named source, so the gap is missing data, never a silent estimate.
      </div>

      <h2>Campaigns</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Campaign</th>
              <th>Status</th>
              <th className="num">Articles</th>
              <th className="num">Outlets</th>
              <th className="num">Days observed</th>
              <th>Last import</th>
            </tr>
          </thead>
          <tbody>
            {campaigns.map((c) => (
              <tr key={c.slug}>
                <td>
                  <Link href={`/campaigns/${c.slug}`}>{c.name}</Link>
                </td>
                <td className="small">{c.status.replace("_", " ")}</td>
                <td className="num">
                  {c.article_count === 0 ? (
                    <span className="muted small">nothing imported</span>
                  ) : (
                    c.article_count
                  )}
                </td>
                <td className="num">{c.outlet_count || "—"}</td>
                <td className="num">{c.max_day_index ?? "—"}</td>
                <td className="small muted nowrap">{formatDate(c.last_import_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>Import ledger</h2>
      <p className="lede small">
        Every article traces to a row here. A re-import that finds nothing new still appears, so
        &quot;we checked and nothing changed&quot; is distinguishable from &quot;nobody
        checked&quot;.
      </p>
      {imports.length === 0 ? (
        <div className="empty-state">
          <strong>Nothing imported.</strong>
          Run <code className="inline">cib import inspect --file …</code> to see a file&apos;s
          columns, then import it.
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th className="num">#</th>
                <th>Source</th>
                <th>File</th>
                <th>Imported</th>
                <th className="num">Rows</th>
                <th className="num">New</th>
                <th className="num">Already present</th>
                <th className="num">Rejected</th>
                <th>By</th>
              </tr>
            </thead>
            <tbody>
              {imports.map((i) => (
                <tr key={i.id}>
                  <td className="num">{i.id}</td>
                  <td className="small">{i.source}</td>
                  <td className="small mono" style={{ maxWidth: "16rem", wordBreak: "break-all" }}>
                    {i.file_name ?? <span className="muted">(API)</span>}
                  </td>
                  <td className="small nowrap">{i.imported_at}</td>
                  <td className="num">{i.row_count}</td>
                  <td className="num">{i.rows_inserted}</td>
                  <td className="num muted">{i.rows_skipped_duplicate}</td>
                  <td className="num" style={{ color: i.rows_rejected ? "var(--warn)" : undefined }}>
                    {i.rows_rejected}
                  </td>
                  <td className="small muted">{i.created_by}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2>Outlets without a sourced reach figure</h2>
      {outlets.outlets.filter((o: any) => o.reach_value === null).length === 0 ? (
        <div className="banner info">Every outlet has a sourced reach figure.</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Outlet</th>
                <th>Tier</th>
                <th>Country</th>
                <th className="num">Articles</th>
              </tr>
            </thead>
            <tbody>
              {outlets.outlets
                .filter((o: any) => o.reach_value === null)
                .map((o: any) => (
                  <tr key={o.id}>
                    <td>{o.name}</td>
                    <td className="small muted">{o.tier}</td>
                    <td className="small">{o.country ?? <span className="muted">unknown</span>}</td>
                    <td className="num">{o.articles}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="small muted">
        Record one with{" "}
        <code className="inline">
          cib outlet reach &lt;id&gt; --value N --source &quot;where it came from&quot;
        </code>
        . The source is mandatory.
      </p>
    </>
  );
}
