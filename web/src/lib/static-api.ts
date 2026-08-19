/**
 * Static data layer: the same `api` surface, served from the committed snapshot.
 *
 * GitHub Pages has no Python process, so the dashboard reads `seed.json` — written by
 * `cib export snapshot` from the same `cib.metrics` functions the live API calls.
 *
 * Two rules hold this together:
 *
 *  1. **Nothing is computed here.** Values, row IDs, caveats and derived cutoffs were all
 *     computed in Python and baked. This module looks things up. The one join it performs —
 *     reattaching each metric's label, unit and definition from `meta` — is deduplication, not
 *     calculation, and it exists because repeating that text across every metric at every cutoff
 *     for every campaign was most of the file size.
 *
 *  2. **Writes refuse loudly.** A snapshot is read-only. Logging an escalation or toggling a watch
 *     rule against it would look like it worked and silently lose the record, so those throw a
 *     `ReadOnlySnapshotError` that the UI renders as an explanation.
 */

import seedData from "./seed.json";
import { ApiError } from "./errors";
import type { Seed, SlimMetric, SlimMetricSet } from "./seed-types";
import type {
  CampaignSummary,
  Comparison,
  Meta,
  Metric,
  MetricSet,
  PrePublicationView,
  SeriesPoint,
} from "./api";

export const seed = seedData as unknown as Seed;

export class ReadOnlySnapshotError extends Error {
  constructor(action: string) {
    super(
      `This is a read-only snapshot, generated ${seed.generated_at}. ${action} needs the live ` +
        `tool: run \`make api\` and \`make web\` locally, or use the \`cib\` command line.`,
    );
    this.name = "ReadOnlySnapshotError";
  }
}

export class SnapshotMissError extends Error {
  constructor(what: string) {
    super(
      `${what} is not in this snapshot. Only the cutoffs and campaign combinations baked by ` +
        `\`cib export snapshot\` are available offline — the live tool can compute any of them.`,
    );
    this.name = "SnapshotMissError";
  }
}

function cutoffKey(atDay?: number | null): string {
  return atDay === null || atDay === undefined ? "lifetime" : String(atDay);
}

/** Reattach the constant per-metric fields that were stripped to deduplicate the snapshot. */
function hydrateMetric(metric: SlimMetric): Metric {
  const definition = seed.meta.definitions[metric.key];
  const staticCaveat = definition?.caveat;
  return {
    ...metric,
    label: definition?.label ?? metric.key,
    unit: definition?.unit ?? "",
    definition: {
      plain: definition?.plain ?? "",
      counts: definition?.counts ?? "",
      caveat: staticCaveat ?? "",
    },
    caveats: staticCaveat ? [...metric.caveats, staticCaveat] : metric.caveats,
  };
}

function hydrateMetricSet(set: SlimMetricSet): MetricSet {
  return {
    ...set,
    metrics: Object.fromEntries(
      Object.entries(set.metrics).map(([key, metric]) => [key, hydrateMetric(metric)]),
    ),
  };
}

function detail(ref: string) {
  const found =
    seed.campaign_detail[ref] ??
    Object.values(seed.campaign_detail).find(
      (d) => String(d.campaign.id) === String(ref) || d.campaign.slug === ref,
    );
  if (!found) throw new SnapshotMissError(`Campaign "${ref}"`);
  return found;
}

const ok = <T>(value: T): Promise<T> => Promise.resolve(value);

export const staticApi = {
  meta: () => ok(seed.meta as Meta),

  campaigns: () => ok(seed.campaigns as CampaignSummary[]),

  campaign: (ref: string) => {
    const d = detail(ref);
    return ok({
      ...d.campaign,
      themes: d.campaign.themes,
      is_pre_publication: d.campaign.is_pre_publication,
      sources: d.sources,
      last_import_at: d.last_import_at,
      last_snapshot_at: d.last_snapshot_at,
      precedents: d.precedents,
    });
  },

  metrics: (ref: string, atDay?: number | null) => {
    const d = detail(ref);
    if (d.pre_publication) {
      // Mirrors the live API's 409 exactly, including the class: pages branch on
      // `instanceof ApiError` to swap in the pre-publication view, so a look-alike error would
      // silently render "could not load" instead.
      return Promise.reject(
        new ApiError(d.pre_publication.footprint_refusal, 409, {
          error: "pre_publication",
          message: d.pre_publication.footprint_refusal,
        }),
      );
    }
    const set = d.metrics_by_cutoff[cutoffKey(atDay)];
    if (!set) throw new SnapshotMissError(`Day-index cutoff "${cutoffKey(atDay)}"`);
    return ok(hydrateMetricSet(set));
  },

  prePublication: (ref: string) => {
    const d = detail(ref);
    if (!d.pre_publication) {
      throw new SnapshotMissError(`A pre-publication view for "${ref}"`);
    }
    return ok(d.pre_publication as PrePublicationView);
  },

  series: (ref: string, atDay?: number | null) => {
    const d = detail(ref);
    return ok({
      campaign_slug: d.campaign.slug as string,
      series: (d.series_by_cutoff[cutoffKey(atDay)] ?? []) as SeriesPoint[],
    });
  },

  compare: (refs: string[], atDay?: number | null): Promise<Comparison> => {
    const slugs = refs.map((r) => detail(r).campaign.slug as string);
    const key = [...[...slugs].sort(), cutoffKey(atDay)].join("|");
    const entry = seed.comparisons[key];
    if (!entry) {
      throw new SnapshotMissError(
        `The comparison ${slugs.join(" vs ")} at cutoff ${cutoffKey(atDay)}`,
      );
    }
    const campaigns = entry.columns.map((column) =>
      hydrateMetricSet(seed.campaign_detail[column.slug].metrics_by_cutoff[column.cutoff]),
    );
    const series: Record<string, SeriesPoint[]> = {};
    for (const column of entry.columns) {
      series[column.slug] =
        seed.campaign_detail[column.slug].series_by_cutoff[column.cutoff] ?? [];
    }
    return ok({
      at_day_index: entry.at_day_index,
      cutoff_was_explicit: entry.cutoff_was_explicit,
      campaigns,
      pre_publication: entry.pre_publication_slugs.map(
        (slug) => seed.campaign_detail[slug].pre_publication as PrePublicationView,
      ),
      caveats: entry.caveats,
      series,
      escalation_series: Object.fromEntries(
        campaigns.map((c) => [
          c.campaign_slug,
          (c.metrics.severity_weighted_total?.basis?.running_total ?? []) as any[],
        ]),
      ),
      metric_order: seed.meta.metric_order,
    });
  },

  evidence: (table: string, ids: number[]) => {
    const store = seed.evidence[table] ?? {};
    const rows = ids.map((id) => store[String(id)]).filter(Boolean);
    return ok({ table, count: rows.length, requested: ids.length, rows });
  },

  outlets: (ref: string) => ok(detail(ref).outlets),

  clusters: (ref: string) => ok(detail(ref).clusters),

  mentions: (ref: string) => ok(detail(ref).mentions),

  escalations: (ref: string) => {
    const d = detail(ref);
    return ok({
      campaign_slug: d.campaign.slug as string,
      escalations: d.escalations,
      severity_anchors: seed.meta.severity_anchors,
      log_note:
        "This log is maintained by hand. An empty log means nothing has been recorded, not " +
        "that nothing has happened.",
    });
  },

  addEscalation: () => Promise.reject(new ReadOnlySnapshotError("Logging an escalation")),
  verifyEscalation: () => Promise.reject(new ReadOnlySnapshotError("Verifying an escalation")),
  setWatchRuleEnabled: () =>
    Promise.reject(new ReadOnlySnapshotError("Enabling or disabling a watch rule")),

  signals: (ref: string) => ok(detail(ref).signals),

  watchRules: () => ok(seed.watch.rules),
  watchHits: () => ok(seed.watch.hits),
  watchStatus: () => ok(seed.watch.status),

  imports: () => ok(seed.imports),
  allOutlets: () => ok(seed.outlets),
};
