"use client";

import type { DataQuality } from "@/lib/api";
import { formatDate } from "@/lib/api";

/**
 * Required on every view that shows a figure.
 *
 * Where the data came from, when it was last imported, and what share of records are missing
 * country or reach values. A figure read without these is a figure read wrong.
 */
export function DataQualityBanner({
  quality,
  label,
}: {
  quality: DataQuality;
  label?: string;
}) {
  const problems: string[] = [];
  if (quality.articles_total_imported === 0) {
    problems.push(
      "No articles have been imported. Every footprint figure here is an empty dataset, not a measurement of low coverage.",
    );
  }
  if (quality.articles_undated) {
    problems.push(
      `${quality.articles_undated} article(s) could not be placed on the day axis and are excluded from every day-truncated figure.`,
    );
  }
  if (quality.outlets_missing_reach_pct != null && quality.outlets_missing_reach_pct > 0) {
    problems.push(
      `${quality.outlets_missing_reach_pct}% of outlets have no sourced reach figure, so any reach total is a floor.`,
    );
  }
  if (quality.articles_missing_country_pct != null && quality.articles_missing_country_pct > 0) {
    problems.push(
      `${quality.articles_missing_country_pct}% of articles have no country and are excluded from the country count.`,
    );
  }
  if (quality.articles_with_body_text_pct != null && quality.articles_with_body_text_pct < 100) {
    problems.push(
      `Only ${quality.articles_with_body_text_pct}% of articles have body text, so mention counts are a floor rather than a total.`,
    );
  }

  const severity = quality.articles_total_imported === 0 ? "danger" : problems.length ? "warn" : "info";

  return (
    <div className={`banner ${severity}`}>
      <strong>Data quality{label ? ` — ${label}` : ""}</strong>
      <div className="small">
        Sources:{" "}
        {quality.sources.length ? (
          quality.sources.map((s, i) => (
            <span key={s.source}>
              {i > 0 && " · "}
              <code className="inline">{s.source}</code> {s.articles} articles,
              last {formatDate(s.last_imported_at)}
            </span>
          ))
        ) : (
          <em>none — nothing imported</em>
        )}
        {" · "}Last import: {formatDate(quality.last_import_at)}
      </div>
      {problems.length > 0 && (
        <ul>
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
