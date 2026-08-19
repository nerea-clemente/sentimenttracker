"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import {
  api,
  ApiError,
  formatDate,
  type Escalation,
  type MetricSet,
  type PrePublicationView,
  type SeriesPoint,
} from "@/lib/api";
import { DataQualityBanner } from "@/components/DataQualityBanner";
import { ErrorBanner, Loading } from "@/components/Loading";
import { LineChart, seriesColor } from "@/components/LineChart";
import { MetricCell } from "@/components/MetricCell";

export default function CampaignDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = use(params);
  const [metrics, setMetrics] = useState<MetricSet | null>(null);
  const [prePub, setPrePub] = useState<PrePublicationView | null>(null);
  const [series, setSeries] = useState<SeriesPoint[]>([]);
  const [outlets, setOutlets] = useState<any[]>([]);
  const [clusters, setClusters] = useState<any[]>([]);
  const [mentions, setMentions] = useState<any[]>([]);
  const [escalations, setEscalations] = useState<Escalation[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const m = await api.metrics(slug);
        if (cancelled) return;
        setMetrics(m);
        const [s, o, c, mm, e] = await Promise.all([
          api.series(slug),
          api.outlets(slug),
          api.clusters(slug),
          api.mentions(slug),
          api.escalations(slug),
        ]);
        if (cancelled) return;
        setSeries(s.series);
        setOutlets(o);
        setClusters(c);
        setMentions(mm);
        setEscalations(e.escalations);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 409) {
          try {
            setPrePub(await api.prePublication(slug));
          } catch (inner: any) {
            setError(inner.message);
          }
        } else {
          setError((err as Error).message);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [slug]);

  if (error) return <ErrorBanner error={error} />;
  if (prePub) return <PrePublicationDetail view={prePub} />;
  if (!metrics) return <Loading what="campaign" />;

  return (
    <>
      <h1>{metrics.campaign_name}</h1>
      <p className="lede mono small">{metrics.campaign_slug}</p>

      <DataQualityBanner quality={metrics.data_quality} />

      {metrics.caveats.length > 0 && (
        <div className="banner warn">
          <strong>Caveats</strong>
          <ul>
            {metrics.caveats.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </div>
      )}

      <h2>Measured footprint</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th style={{ minWidth: "14rem" }}>Metric</th>
              <th className="num">Value</th>
              <th className="num">Source rows</th>
              <th>What it counts</th>
            </tr>
          </thead>
          <tbody>
            {Object.values(metrics.metrics).map((m) => (
              <tr key={m.key}>
                <th scope="row" style={{ fontWeight: 500, background: "var(--surface)" }}>
                  {m.label}
                  <span className="muted small"> · {m.unit}</span>
                </th>
                <td className="num">
                  <MetricCell metric={m} campaignLabel={metrics.campaign_name} />
                </td>
                <td className="num muted small">{m.row_count}</td>
                <td className="small muted">{m.definition.counts}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>Timeline</h2>
      <div className="card">
        <LineChart
          lines={[
            {
              label: "articles per day",
              color: seriesColor(0),
              points: series.map((p) => ({ x: p.day_index, y: p.articles })),
            },
            {
              label: "cumulative articles",
              color: seriesColor(1),
              points: series.map((p) => ({ x: p.day_index, y: p.cumulative_articles })),
            },
          ]}
          yLabel="articles"
        />
      </div>

      <h2>Outlets</h2>
      {outlets.length === 0 ? (
        <div className="empty-state">
          <strong>No outlets.</strong>Nothing has been imported for this campaign yet.
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Outlet</th>
                <th>Tier</th>
                <th>Country</th>
                <th className="num">Articles</th>
                <th className="num">Original</th>
                <th className="num">Reach</th>
                <th>Reach source</th>
              </tr>
            </thead>
            <tbody>
              {outlets.map((o) => (
                <tr key={o.id}>
                  <td>{o.name}</td>
                  <td className="small muted">{o.tier}</td>
                  <td className="small">{o.country ?? <span className="muted">unknown</span>}</td>
                  <td className="num">{o.articles}</td>
                  <td className="num">{o.original_articles}</td>
                  <td className="num">
                    {o.reach_value != null ? (
                      <>
                        {Number(o.reach_value).toLocaleString()}
                        {o.reach_is_estimated ? (
                          <span className="pill" style={{ marginLeft: "0.3rem" }}>
                            estimated
                          </span>
                        ) : null}
                      </>
                    ) : (
                      <span className="muted small">no sourced figure</span>
                    )}
                  </td>
                  <td className="small muted">{o.reach_source ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2>Syndication clusters</h2>
      {clusters.length === 0 ? (
        <div className="empty-state">
          <strong>No syndication detected.</strong>
          Every imported article is a distinct story, so unique stories and unique outlets differ
          only by outlets carrying more than one piece.
        </div>
      ) : (
        clusters.map((cluster) => (
          <div className="card" key={cluster.id}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <strong>{cluster.representative_headline}</strong>
              <span className="pill">
                {cluster.member_count} outlets · detected by {cluster.method}
              </span>
            </div>
            <div className="table-wrap" style={{ marginTop: "0.6rem" }}>
              <table>
                <thead>
                  <tr>
                    <th>Outlet</th>
                    <th>Date</th>
                    <th>Headline</th>
                    <th>Role</th>
                    <th className="num">Similarity</th>
                  </tr>
                </thead>
                <tbody>
                  {cluster.members.map((m: any) => (
                    <tr key={m.article_id}>
                      <td>{m.outlet}</td>
                      <td className="nowrap small">{formatDate(m.published_at)}</td>
                      <td>
                        {m.url ? (
                          <a href={m.url} target="_blank" rel="noreferrer">
                            {m.headline}
                          </a>
                        ) : (
                          m.headline
                        )}
                      </td>
                      <td className="small">
                        {m.is_original ? "representative" : <span className="muted">duplicate</span>}
                      </td>
                      <td className="num small muted">
                        {m.similarity != null ? m.similarity.toFixed(2) : "—"} ({m.method})
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))
      )}

      <h2>Entity mentions</h2>
      {mentions.length === 0 ? (
        <div className="empty-state">
          <strong>No mentions recorded.</strong>
          Either no entities are configured, or the imported articles carry no body text to scan.
          Run <code className="inline">cib entity match {metrics.campaign_slug}</code>.
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th className="num">Day</th>
                <th>Entity</th>
                <th>Role</th>
                <th>Evidence sentence</th>
                <th>Classified by</th>
                <th>Outlet</th>
              </tr>
            </thead>
            <tbody>
              {mentions.map((m) => (
                <tr key={m.id}>
                  <td className="num">{m.day_index ?? "—"}</td>
                  <td>
                    {m.entity_name}
                    <div className="small muted">{m.entity_type}</div>
                  </td>
                  <td className="small">{m.role}</td>
                  <td style={{ maxWidth: "30rem" }} className="small">
                    {m.evidence_sentence}
                  </td>
                  <td className="small muted">
                    {m.classified_by}
                    {m.confidence != null && ` · ${m.confidence}`}
                  </td>
                  <td className="small">
                    {m.url ? (
                      <a href={m.url} target="_blank" rel="noreferrer">
                        {m.outlet}
                      </a>
                    ) : (
                      m.outlet
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2>Escalation chain</h2>
      <EscalationChain metrics={metrics} escalations={escalations} />

      <h2>Export</h2>
      <div className="row">
        <a className="btn" href={api.exportUrls.table(slug, "articles")}>
          Articles (CSV)
        </a>
        <a className="btn" href={api.exportUrls.table(slug, "escalations")}>
          Escalations (CSV)
        </a>
        <a className="btn" href={api.exportUrls.table(slug, "mentions")}>
          Mentions (CSV)
        </a>
        <a
          className="btn primary"
          href={api.exportUrls.briefing([slug])}
          target="_blank"
          rel="noreferrer"
        >
          One-page briefing
        </a>
      </div>
    </>
  );
}

function EscalationChain({
  metrics,
  escalations,
}: {
  metrics: MetricSet;
  escalations: Escalation[];
}) {
  const chain = metrics.metrics.escalation_chain?.basis?.chain ?? [];
  return (
    <>
      <div className="card">
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
          {chain.map((stage: any) => (
            <div
              key={stage.stage}
              style={{
                flex: "1 1 9rem",
                padding: "0.6rem 0.7rem",
                borderRadius: "var(--radius)",
                border: `1px solid ${stage.reached ? "var(--accent)" : "var(--border)"}`,
                background: stage.reached ? "var(--accent-soft)" : "var(--surface-2)",
                opacity: stage.reached ? 1 : 0.6,
              }}
            >
              <div style={{ fontWeight: 600, fontSize: "0.82rem" }}>
                {stage.stage.replace(/_/g, " ")}
              </div>
              <div className="small muted">
                {stage.reached
                  ? `day ${stage.day_index ?? "?"} · ${formatDate(stage.first_at)}`
                  : "not reached"}
              </div>
            </div>
          ))}
        </div>
      </div>

      {escalations.length === 0 ? (
        <div className="empty-state">
          <strong>Nothing logged.</strong>
          This log is maintained by hand: an empty log means nothing has been recorded, not that
          nothing has happened. <Link href="/escalations">Log an escalation</Link>.
        </div>
      ) : (
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
              {escalations.map((e) => (
                <tr key={e.id}>
                  <td className="nowrap small">{formatDate(e.occurred_at)}</td>
                  <td className="small">{e.escalation_type.replace(/_/g, " ")}</td>
                  <td>{e.actor_name}</td>
                  <td className="num">{e.severity}</td>
                  <td className="small" style={{ maxWidth: "24rem" }}>
                    {e.description}
                  </td>
                  <td className="small">
                    <a href={e.source_url} target="_blank" rel="noreferrer">
                      source
                    </a>
                  </td>
                  <td className="small muted">{e.verified_by ?? "unverified"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function PrePublicationDetail({ view }: { view: PrePublicationView }) {
  return (
    <>
      <h1>{view.campaign.name}</h1>
      <p className="lede mono small">{view.campaign.slug}</p>

      <div className="banner danger">
        <strong>No footprint — this campaign has not published</strong>
        {view.footprint_refusal}
      </div>

      <div className="card">
        <div className="small">
          <strong>Publisher:</strong> {view.campaign.publisher_org} ·{" "}
          <strong>Type:</strong> {view.campaign.campaign_type} ·{" "}
          <strong>First internal signal:</strong> {formatDate(view.campaign.first_signal_at)}
        </div>
        {view.campaign.themes.length > 0 && (
          <div className="small muted" style={{ marginTop: "0.4rem" }}>
            Themes: {view.campaign.themes.join(", ")}
          </div>
        )}
        {view.campaign.notes && (
          <p className="small" style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
            {view.campaign.notes}
          </p>
        )}
      </div>

      <h2>Publisher precedent</h2>
      <p className="lede">
        What this publisher&apos;s previous investigations actually measured. This is the closest
        thing to a forecast the evidence supports.
      </p>
      {view.publisher_precedent.length === 0 ? (
        <div className="empty-state">
          <strong>No precedent linked.</strong>
          Link a past campaign with{" "}
          <code className="inline">cib campaign precedent {view.campaign.slug} &lt;past&gt; --rationale &quot;…&quot;</code>.
        </div>
      ) : (
        view.publisher_precedent.map((p) => (
          <div className="card" key={p.campaign_id}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <div>
                <Link href={`/campaigns/${p.slug}`}>
                  <strong>{p.name}</strong>
                </Link>
                <div className="small muted">
                  {p.publisher_org} · published {formatDate(p.published_at)}
                </div>
              </div>
            </div>
            <p className="small muted" style={{ marginTop: "0.4rem" }}>
              Linked as precedent by {p.asserted_by}: {p.rationale}
            </p>
            {p.caveats && p.caveats.length > 0 && (
              <div className="banner warn">
                <ul style={{ margin: 0 }}>
                  {p.caveats.map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
              </div>
            )}
            {p.footprint && p.has_measured_footprint !== false ? (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Metric</th>
                      <th className="num">Value</th>
                      <th className="num">Source rows</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.values(p.footprint).map((m) => (
                      <tr key={m.key}>
                        <td>{m.label}</td>
                        <td className="num">
                          <MetricCell metric={m} campaignLabel={p.name} />
                        </td>
                        <td className="num muted small">{m.row_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="empty-state">
                <strong>This precedent has not been measured yet.</strong>
                No coverage has been imported for it, so it has no footprint to compare against —
                which is not the same as its footprint having been small. Import its archive
                export first.
              </div>
            )}
          </div>
        ))
      )}

      <h2>Logged internal signals</h2>
      {view.inbound_signals.length === 0 ? (
        <div className="empty-state">
          <strong>Nothing logged.</strong>
          Log journalist enquiries, customer questionnaires, tender questions and investor queries
          with <code className="inline">cib signal</code>.
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Channel</th>
                <th>Summary</th>
                <th>Reference</th>
                <th>Logged by</th>
              </tr>
            </thead>
            <tbody>
              {view.inbound_signals.map((s) => (
                <tr key={s.id}>
                  <td className="nowrap small">{formatDate(s.occurred_at)}</td>
                  <td className="small">{s.channel}</td>
                  <td>{s.summary}</td>
                  <td className="small muted">{s.source_ref ?? "—"}</td>
                  <td className="small muted">{s.logged_by}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2>Watch rules</h2>
      <p className="lede">
        For a campaign that has not published, the tool&apos;s job is to catch day zero.
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Rule</th>
              <th>Type</th>
              <th>Pattern</th>
              <th>State</th>
              <th className="num">Hits</th>
              <th>Last polled</th>
            </tr>
          </thead>
          <tbody>
            {view.watch_rules.map((r) => (
              <tr key={r.id}>
                <td>
                  {r.name}
                  {r.promotes_to_live ? (
                    <span className="pill live" style={{ marginLeft: "0.35rem" }}>
                      promotes to live
                    </span>
                  ) : null}
                </td>
                <td className="small">{r.rule_type}</td>
                <td className="small mono" style={{ maxWidth: "20rem", wordBreak: "break-all" }}>
                  {r.pattern}
                </td>
                <td>
                  <span className={`pill ${r.enabled ? "live" : "pre"}`}>
                    {r.enabled ? "enabled" : "disabled"}
                  </span>
                </td>
                <td className="num">{r.hits}</td>
                <td className="small muted nowrap">
                  {r.last_polled_at ? formatDate(r.last_polled_at) : "never"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {view.watch_rules_enabled === 0 && view.watch_rules.length > 0 && (
        <div className="banner danger" style={{ marginTop: "0.75rem" }}>
          <strong>Nothing is being watched</strong>
          Every rule for this campaign is disabled, so day zero will not be caught. Enable them on
          the <Link href="/watchlist">watchlist</Link> once they point at real feeds.
        </div>
      )}

      <h2>Export</h2>
      <a
        className="btn primary"
        href={api.exportUrls.briefing([view.campaign.slug])}
        target="_blank"
        rel="noreferrer"
      >
        One-page briefing
      </a>
    </>
  );
}
