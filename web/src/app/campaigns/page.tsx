"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, formatDate, type CampaignSummary } from "@/lib/api";
import { ErrorBanner, Loading } from "@/components/Loading";

export default function CampaignsPage() {
  const [campaigns, setCampaigns] = useState<CampaignSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.campaigns().then(setCampaigns).catch((e) => setError(e.message));
  }, []);

  if (error) return <ErrorBanner error={error} />;
  if (!campaigns) return <Loading what="campaigns" />;

  return (
    <>
      <h1>Campaigns</h1>
      <p className="lede">
        Every campaign the tool tracks. A campaign with no publication date has no footprint and is
        marked accordingly — it opens a pre-publication view, not a table of zeros.
      </p>

      {campaigns.length === 0 ? (
        <div className="empty-state">
          <strong>No campaigns yet.</strong>
          Run <code className="inline">cib seed</code> to create the three starter records, then
          import real coverage with <code className="inline">cib import</code>.
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Campaign</th>
                <th>Publisher</th>
                <th>Status</th>
                <th>Published</th>
                <th className="num">Articles</th>
                <th className="num">Outlets</th>
                <th className="num">Escalations</th>
                <th className="num">Days observed</th>
                <th>Last import</th>
              </tr>
            </thead>
            <tbody>
              {campaigns.map((c) => (
                <tr key={c.slug}>
                  <td>
                    <Link href={`/campaigns/${c.slug}`}>{c.name}</Link>
                    <div className="small muted mono">{c.slug}</div>
                  </td>
                  <td className="small">{c.publisher_org}</td>
                  <td>
                    <span
                      className={`pill ${
                        c.status === "pre_publication"
                          ? "pre"
                          : c.status === "live"
                            ? "live"
                            : "archived"
                      }`}
                    >
                      {c.status.replace("_", " ")}
                    </span>
                  </td>
                  <td className="nowrap small">{formatDate(c.published_at)}</td>
                  <td className="num">
                    {c.article_count === 0 ? (
                      <span className="muted" title="Nothing imported yet — not a measurement of low coverage">
                        none imported
                      </span>
                    ) : (
                      c.article_count.toLocaleString()
                    )}
                  </td>
                  <td className="num">{c.outlet_count || "—"}</td>
                  <td className="num">{c.escalation_count || "—"}</td>
                  <td className="num">{c.max_day_index ?? "—"}</td>
                  <td className="small muted nowrap">{formatDate(c.last_import_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
