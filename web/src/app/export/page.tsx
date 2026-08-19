"use client";

import { useEffect, useState } from "react";
import { api, type CampaignSummary } from "@/lib/api";
import { ErrorBanner, Loading } from "@/components/Loading";
import { ExportLink } from "@/components/ExportLink";

/**
 * Export.
 *
 * CSV of any table, and the one-page briefing intended for a crisis or leadership team. Every
 * export carries its provenance header, because a CSV that circulates without one is a CSV that
 * gets quoted without one.
 */
export default function ExportPage() {
  const [campaigns, setCampaigns] = useState<CampaignSummary[] | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [cutoff, setCutoff] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .campaigns()
      .then((rows) => {
        setCampaigns(rows);
        if (rows.length) setSelected([rows[0].slug]);
      })
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <ErrorBanner error={error} />;
  if (!campaigns) return <Loading what="campaigns" />;

  const atDay = cutoff.trim() === "" ? null : Number(cutoff);
  const primary = selected[0];

  const toggle = (slug: string) =>
    setSelected((current) =>
      current.includes(slug)
        ? current.filter((s) => s !== slug)
        : current.length >= 4
          ? current
          : [...current, slug],
    );

  return (
    <>
      <h1>Export</h1>
      <p className="lede">
        Everything here can be handed to someone who was not in the room and defended line by line:
        each figure carries its definition, its source row count and its caveats.
      </p>

      <div className="card">
        <label>Campaigns</label>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem", marginBottom: "0.75rem" }}>
          {campaigns.map((c) => (
            <button
              key={c.slug}
              className={selected.includes(c.slug) ? "primary" : ""}
              onClick={() => toggle(c.slug)}
            >
              {c.name.length > 42 ? `${c.name.slice(0, 42)}…` : c.name}
            </button>
          ))}
        </div>
        <div className="field" style={{ maxWidth: "12rem", marginBottom: 0 }}>
          <label htmlFor="cutoff">Day-index cutoff</label>
          <input
            id="cutoff"
            type="number"
            placeholder="auto"
            value={cutoff}
            onChange={(e) => setCutoff(e.target.value)}
          />
        </div>
      </div>

      <h2>Briefing</h2>
      <div className="card">
        <p className="small muted" style={{ marginTop: 0 }}>
          Campaign summary, comparison table, escalation timeline and evidence appendix. The first
          selected campaign is the subject; any others become its comparison columns.
        </p>
        <div className="row">
          <ExportLink primary href={api.exportUrls.briefing(selected, atDay, "html")}>
            Briefing (HTML, printable)
          </ExportLink>
          <ExportLink href={api.exportUrls.briefing(selected, atDay, "markdown")}>
            Briefing (Markdown)
          </ExportLink>
        </div>
      </div>

      <h2>Tables</h2>
      <div className="card">
        <div className="row">
          <ExportLink href={selected.length < 2 ? null : api.exportUrls.comparison(selected, atDay)}>
            Comparison table (CSV)
          </ExportLink>
          {primary && (
            <>
              <ExportLink href={api.exportUrls.table(primary, "articles")}>
                Articles (CSV)
              </ExportLink>
              <ExportLink href={api.exportUrls.table(primary, "escalations")}>
                Escalations (CSV)
              </ExportLink>
              <ExportLink href={api.exportUrls.table(primary, "mentions")}>
                Mentions (CSV)
              </ExportLink>
            </>
          )}
        </div>
        {selected.length < 2 && (
          <p className="small muted" style={{ marginBottom: 0, marginTop: "0.6rem" }}>
            The comparison CSV needs at least two campaigns.
          </p>
        )}
        <p className="small muted" style={{ marginBottom: 0, marginTop: "0.6rem" }}>
          Article, escalation and mention exports apply to the first selected campaign.
        </p>
      </div>
    </>
  );
}
