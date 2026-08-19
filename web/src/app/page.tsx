"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  api,
  formatDate,
  type CampaignSummary,
  type Comparison,
} from "@/lib/api";
import { DataQualityBanner } from "@/components/DataQualityBanner";
import { ErrorBanner, Loading } from "@/components/Loading";
import { LineChart, seriesColor, type Line } from "@/components/LineChart";
import { ExportLink } from "@/components/ExportLink";
import { MetricCell } from "@/components/MetricCell";

/**
 * The primary screen.
 *
 * Select two to four campaigns, pick a day-index cutoff, get a measured table. This is the format
 * that replaces "low / medium / high": every cell is a number with its source rows one click away,
 * or a stated reason the figure is not available.
 */
export default function ComparePage() {
  const [campaigns, setCampaigns] = useState<CampaignSummary[] | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [cutoffInput, setCutoffInput] = useState<string>("");
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .campaigns()
      .then((rows) => {
        setCampaigns(rows);
        // Default to the two most recently published campaigns that actually carry coverage.
        const withData = rows.filter((c) => c.published_at && c.article_count > 0);
        setSelected(withData.slice(0, 2).map((c) => c.slug));
      })
      .catch((e) => setError(e.message));
  }, []);

  const run = useCallback(async () => {
    if (selected.length < 2) return;
    setBusy(true);
    setError(null);
    try {
      const cutoff = cutoffInput.trim() === "" ? null : Number(cutoffInput);
      setComparison(await api.compare(selected, cutoff));
    } catch (e: any) {
      setError(e.message);
      setComparison(null);
    } finally {
      setBusy(false);
    }
  }, [selected, cutoffInput]);

  useEffect(() => {
    if (selected.length >= 2) void run();
    // Only re-run when the selection changes; the cutoff is applied on Compare.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  const toggle = (slug: string) =>
    setSelected((current) =>
      current.includes(slug)
        ? current.filter((s) => s !== slug)
        : current.length >= 4
          ? current
          : [...current, slug],
    );

  const charts = useMemo(() => buildCharts(comparison), [comparison]);

  if (error && !campaigns) return <ErrorBanner error={error} />;
  if (!campaigns) return <Loading what="campaigns" />;

  const withData = campaigns.filter((c) => c.published_at);
  const prePublication = campaigns.filter((c) => !c.published_at);

  return (
    <>
      <h1>Campaign comparison</h1>
      <p className="lede">
        Two to four campaigns, truncated at the same day index, so a campaign at day 5 is compared
        against past campaigns at <em>their</em> day 5 rather than their lifetime totals. Every
        figure opens the rows that produced it.
      </p>

      <div className="card">
        <div className="row" style={{ marginBottom: "0.75rem" }}>
          <div style={{ flex: "1 1 24rem" }}>
            <label>Campaigns (2–4)</label>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem" }}>
              {withData.map((c) => (
                <button
                  key={c.slug}
                  onClick={() => toggle(c.slug)}
                  className={selected.includes(c.slug) ? "primary" : ""}
                  title={`${c.article_count} articles, published ${formatDate(c.published_at)}`}
                >
                  {c.name.length > 44 ? `${c.name.slice(0, 44)}…` : c.name}
                  <span style={{ opacity: 0.7 }}> · {c.article_count}</span>
                </button>
              ))}
              {prePublication.map((c) => (
                <button
                  key={c.slug}
                  onClick={() => toggle(c.slug)}
                  className={selected.includes(c.slug) ? "primary" : ""}
                  title="Pre-publication: shown as publisher precedent and logged signals, never as a footprint"
                >
                  {c.name.length > 40 ? `${c.name.slice(0, 40)}…` : c.name}
                  <span className="pill pre" style={{ marginLeft: "0.35rem" }}>
                    pre-pub
                  </span>
                </button>
              ))}
            </div>
          </div>
          <div className="field" style={{ marginBottom: 0 }}>
            <label htmlFor="cutoff">Day-index cutoff</label>
            <input
              id="cutoff"
              type="number"
              placeholder="auto"
              value={cutoffInput}
              onChange={(e) => setCutoffInput(e.target.value)}
              style={{ width: "7rem" }}
            />
          </div>
          <button className="primary" onClick={() => void run()} disabled={selected.length < 2 || busy}>
            {busy ? "Comparing…" : "Compare"}
          </button>
        </div>
        <p className="small muted" style={{ margin: 0 }}>
          Leave the cutoff blank and the shortest observed window across the selected campaigns is
          used automatically, so no campaign is measured against a longer run of another.
        </p>
      </div>

      {selected.length < 2 && (
        <div className="empty-state">
          <strong>Select at least two campaigns.</strong>
          A single campaign&apos;s numbers mean little on their own — that is the problem this tool
          exists to fix.
        </div>
      )}

      {error && <ErrorBanner error={error} />}

      {comparison && <ComparisonBody comparison={comparison} charts={charts} />}
    </>
  );
}

function ComparisonBody({
  comparison,
  charts,
}: {
  comparison: Comparison;
  charts: { articles: Line[]; outlets: Line[]; countries: Line[] };
}) {
  const columns = comparison.campaigns;

  return (
    <>
      <div className={comparison.cutoff_was_explicit ? "banner info" : "banner warn"}>
        <strong>
          {comparison.at_day_index === null
            ? "No cutoff applied"
            : `Compared at day ${comparison.at_day_index}`}
        </strong>
        {comparison.cutoff_was_explicit
          ? "You set this cutoff. Every campaign below is truncated to it."
          : "This cutoff was derived, not chosen. See the caveats below."}
      </div>

      {comparison.caveats.length > 0 && (
        <div className="banner warn">
          <strong>Caveats — these must travel with the figures below</strong>
          <ul>
            {comparison.caveats.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </div>
      )}

      {comparison.pre_publication.map((view) => (
        <div className="banner danger" key={view.campaign.slug}>
          <strong>{view.campaign.name} — no footprint</strong>
          {view.footprint_refusal}
          <div className="small" style={{ marginTop: "0.5rem" }}>
            <Link href={`/campaigns/${view.campaign.slug}`}>
              Open its pre-publication view
            </Link>{" "}
            — {view.precedent_count} publisher precedent(s), {view.inbound_signal_count} logged
            signal(s), {view.watch_rules_enabled} of {view.watch_rules.length} watch rule(s)
            enabled.
          </div>
        </div>
      ))}

      {columns.length > 0 && (
        <>
          <h2>Measured comparison</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th style={{ minWidth: "14rem" }}>Metric</th>
                  {columns.map((c) => (
                    <th key={c.campaign_slug} className="num">
                      <Link href={`/campaigns/${c.campaign_slug}`}>{c.campaign_name}</Link>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {comparison.metric_order.map((key) => {
                  const sample = columns.find((c) => c.metrics[key])?.metrics[key];
                  if (!sample) return null;
                  return (
                    <tr key={key}>
                      <th
                        scope="row"
                        style={{ fontWeight: 500, background: "var(--surface)" }}
                        title={`${sample.definition.plain}\n\nCounts: ${sample.definition.counts}`}
                      >
                        {sample.label}
                        <span className="muted small"> · {sample.unit}</span>
                      </th>
                      {columns.map((c) => (
                        <td key={c.campaign_slug} className="num">
                          <MetricCell
                            metric={c.metrics[key]}
                            campaignLabel={c.campaign_name}
                          />
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="small muted" style={{ marginTop: "0.5rem" }}>
            Every value above is a button: click it for the article, mention or escalation rows it
            was computed from, with their import provenance.
          </p>

          <h2>Day-aligned cumulative coverage</h2>
          <div className="grid-2">
            <div className="card">
              <h3 style={{ marginTop: 0 }}>Cumulative articles</h3>
              <LineChart lines={charts.articles} yLabel="articles" />
            </div>
            <div className="card">
              <h3 style={{ marginTop: 0 }}>Cumulative unique outlets</h3>
              <LineChart lines={charts.outlets} yLabel="outlets" />
            </div>
            <div className="card">
              <h3 style={{ marginTop: 0 }}>Cumulative countries</h3>
              <LineChart lines={charts.countries} yLabel="countries" />
            </div>
          </div>

          <h2>Data quality</h2>
          {columns.map((c) => (
            <DataQualityBanner
              key={c.campaign_slug}
              quality={c.data_quality}
              label={c.campaign_name}
            />
          ))}

          <h2>Export</h2>
          <div className="row">
            <ExportLink
              href={api.exportUrls.comparison(
                columns.map((c) => c.campaign_slug),
                comparison.at_day_index,
              )}
            >
              Comparison table (CSV)
            </ExportLink>
            <ExportLink
              primary
              href={api.exportUrls.briefing(
                columns.map((c) => c.campaign_slug),
                comparison.at_day_index,
              )}
            >
              One-page briefing (HTML)
            </ExportLink>
          </div>
        </>
      )}
    </>
  );
}

function buildCharts(comparison: Comparison | null) {
  const empty = { articles: [] as Line[], outlets: [] as Line[], countries: [] as Line[] };
  if (!comparison) return empty;
  const slugs = Object.keys(comparison.series);
  const build = (pick: (p: any) => number): Line[] =>
    slugs.map((slug, i) => ({
      label: slug,
      color: seriesColor(i),
      points: comparison.series[slug].map((p) => ({ x: p.day_index, y: pick(p) })),
    }));
  return {
    articles: build((p) => p.cumulative_articles),
    outlets: build((p) => p.cumulative_unique_outlets),
    countries: build((p) => p.cumulative_countries),
  };
}
