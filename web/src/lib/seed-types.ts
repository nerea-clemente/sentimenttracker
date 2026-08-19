/**
 * Shape of the build-time snapshot written by `cib export snapshot`.
 *
 * Every number in it was computed by `cib.metrics` in Python. Nothing here is recomputed in
 * TypeScript — the static data layer selects from what was baked, and joins the constant
 * per-metric fields (label, unit, definition) back on from `meta`, which is a lookup rather than
 * a calculation. That is what keeps "no metric logic in the frontend" true in static mode too.
 */

import type {
  CampaignSummary,
  DataQuality,
  Escalation,
  Meta,
  Metric,
  MetricSet,
  PrePublicationView,
  SeriesPoint,
} from "./api";

/** A metric as stored in the snapshot: label/unit/definition stripped, rejoined from `meta`. */
export type SlimMetric = Omit<Metric, "label" | "unit" | "definition">;

export interface SlimMetricSet extends Omit<MetricSet, "metrics"> {
  metrics: Record<string, SlimMetric>;
}

export interface CampaignDetail {
  campaign: Record<string, any>;
  sources: DataQuality["sources"];
  last_import_at: string | null;
  last_snapshot_at: string | null;
  precedents: any[];
  escalations: Escalation[];
  signals: any[];
  max_observed_day_index: number | null;
  pre_publication: PrePublicationView | null;
  cutoffs?: string[];
  metrics_by_cutoff: Record<string, SlimMetricSet>;
  series_by_cutoff: Record<string, SeriesPoint[]>;
  outlets: any[];
  clusters: any[];
  mentions: any[];
}

export interface ComparisonEntry {
  requested_cutoff: string;
  at_day_index: number | null;
  cutoff_was_explicit: boolean;
  caveats: string[];
  columns: { slug: string; cutoff: string }[];
  pre_publication_slugs: string[];
}

export interface Seed {
  generated_at: string;
  generated_by: string;
  mode: "snapshot";
  warnings: string[];
  meta: Meta;
  stats: Record<string, number>;
  cutoffs: string[];
  evidence: Record<string, Record<string, any>>;
  campaigns: CampaignSummary[];
  campaign_detail: Record<string, CampaignDetail>;
  comparisons: Record<string, ComparisonEntry>;
  outlets: {
    outlets: any[];
    count: number;
    with_sourced_reach: number;
    reach_coverage_pct: number | null;
  };
  imports: any[];
  watch: { rules: any[]; hits: any[]; status: any };
}
