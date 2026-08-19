/**
 * API client.
 *
 * Every figure the dashboard renders comes from here. The frontend computes no metric of its own:
 * the Python metrics module is the single definition of every number in the system, and this file
 * is the only place that talks to it.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch (cause) {
    throw new ApiError(
      `Could not reach the API at ${API_BASE}. Start it with \`cib serve\`.`,
      0,
      cause,
    );
  }
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new ApiError(
      typeof body?.detail === "string"
        ? body.detail
        : `${response.status} from ${path}`,
      response.status,
      body?.detail,
    );
  }
  return body as T;
}

/* ---------------------------------------------------------------- types */

export interface MetricDefinition {
  plain: string;
  counts: string;
  caveat: string;
}

export interface Metric {
  key: string;
  label: string;
  unit: string;
  value: unknown;
  available: boolean;
  unavailable_reason: string;
  row_table: string;
  row_ids: number[];
  row_count: number;
  basis: Record<string, any>;
  caveats: string[];
  definition: MetricDefinition;
  computed_at: string;
}

export interface DataQuality {
  articles_in_window: number;
  articles_total_imported: number;
  articles_undated: number;
  articles_missing_country_pct: number | null;
  articles_with_body_text_pct: number | null;
  outlets_missing_reach_pct: number | null;
  last_import_at: string | null;
  sources: {
    source: string;
    articles: number;
    last_imported_at: string;
    import_count: number;
  }[];
}

export interface MetricSet {
  campaign_id: number;
  campaign_slug: string;
  campaign_name: string;
  at_day_index: number | null;
  metrics: Record<string, Metric>;
  caveats: string[];
  data_quality: DataQuality;
}

export interface SeriesPoint {
  day_index: number;
  articles: number;
  cumulative_articles: number;
  unique_outlets_today: number;
  cumulative_unique_outlets: number;
  cumulative_countries: number;
}

export interface PrePublicationView {
  mode: "pre_publication";
  campaign: {
    id: number;
    slug: string;
    name: string;
    publisher_org: string;
    campaign_type: string;
    status: string;
    first_signal_at: string | null;
    themes: string[];
    notes: string | null;
  };
  footprint_available: false;
  footprint_refusal: string;
  publisher_precedent: {
    campaign_id: number;
    slug: string;
    name: string;
    publisher_org: string;
    published_at: string | null;
    rationale: string;
    asserted_by: string;
    footprint?: Record<string, Metric>;
    data_quality?: DataQuality;
    caveats?: string[];
    has_measured_footprint?: boolean;
  }[];
  precedent_count: number;
  inbound_signals: {
    id: number;
    occurred_at: string;
    channel: string;
    summary: string;
    logged_by: string;
    source_ref: string | null;
  }[];
  inbound_signal_count: number;
  watch_rules: {
    id: number;
    name: string;
    rule_type: string;
    pattern: string;
    enabled: number;
    promotes_to_live: number;
    last_polled_at: string | null;
    last_error: string | null;
    hits: number;
  }[];
  watch_rules_enabled: number;
  watch_hits: { id: number; matched_url: string; matched_title: string | null; detected_at: string; rule_name: string }[];
}

export interface Comparison {
  at_day_index: number | null;
  cutoff_was_explicit: boolean;
  campaigns: MetricSet[];
  pre_publication: PrePublicationView[];
  caveats: string[];
  series: Record<string, SeriesPoint[]>;
  escalation_series: Record<string, { day_index: number | null; running_total: number; severity: number; escalation_id: number }[]>;
  metric_order: string[];
}

export interface CampaignSummary {
  id: number;
  name: string;
  slug: string;
  publisher_org: string;
  campaign_type: string;
  status: string;
  published_at: string | null;
  first_signal_at: string | null;
  timezone: string;
  themes: string;
  notes: string | null;
  article_count: number;
  outlet_count: number;
  escalation_count: number;
  max_day_index: number | null;
  last_import_at: string | null;
}

export interface Meta {
  metric_order: string[];
  definitions: Record<string, { key: string; label: string; unit: string } & MetricDefinition>;
  severity_anchors: Record<string, string>;
  role_weights: Record<string, number>;
  outlet_tiers: string[];
  escalation_types: string[];
  inbound_channels: string[];
  mention_roles: string[];
  watch_rule_types: string[];
  last_import_at: string | null;
}

export interface Escalation {
  id: number;
  campaign_id: number;
  occurred_at: string;
  escalation_type: string;
  actor_name: string;
  actor_type: string | null;
  description: string;
  source_url: string;
  severity: number;
  verified_by: string | null;
  verified_at: string | null;
  created_by: string;
  created_at: string;
}

/* ---------------------------------------------------------------- calls */

export const api = {
  meta: () => request<Meta>("/api/meta"),

  campaigns: () => request<CampaignSummary[]>("/api/campaigns"),

  campaign: (ref: string) => request<any>(`/api/campaigns/${ref}`),

  metrics: (ref: string, atDay?: number | null) =>
    request<MetricSet>(
      `/api/campaigns/${ref}/metrics${atDay != null ? `?at_day=${atDay}` : ""}`,
    ),

  prePublication: (ref: string) =>
    request<PrePublicationView>(`/api/campaigns/${ref}/pre-publication`),

  series: (ref: string, atDay?: number | null) =>
    request<{ campaign_slug: string; series: SeriesPoint[] }>(
      `/api/campaigns/${ref}/series${atDay != null ? `?at_day=${atDay}` : ""}`,
    ),

  compare: (refs: string[], atDay?: number | null) => {
    const params = new URLSearchParams();
    refs.forEach((r) => params.append("campaign", r));
    if (atDay != null) params.set("at_day", String(atDay));
    return request<Comparison>(`/api/compare?${params}`);
  },

  evidence: (table: string, ids: number[]) =>
    request<{ table: string; count: number; requested: number; rows: any[] }>(
      `/api/evidence?table=${table}&ids=${ids.join(",")}`,
    ),

  outlets: (ref: string, atDay?: number | null) =>
    request<any[]>(
      `/api/campaigns/${ref}/outlets${atDay != null ? `?at_day=${atDay}` : ""}`,
    ),

  clusters: (ref: string) => request<any[]>(`/api/campaigns/${ref}/clusters`),

  mentions: (ref: string, atDay?: number | null) =>
    request<any[]>(
      `/api/campaigns/${ref}/mentions${atDay != null ? `?at_day=${atDay}` : ""}`,
    ),

  escalations: (ref: string) =>
    request<{ campaign_slug: string; escalations: Escalation[]; severity_anchors: Record<string, string>; log_note: string }>(
      `/api/campaigns/${ref}/escalations`,
    ),

  addEscalation: (ref: string, payload: Record<string, unknown>) =>
    request<Escalation>(`/api/campaigns/${ref}/escalations`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  verifyEscalation: (id: number, verifiedBy?: string) =>
    request<Escalation>(`/api/escalations/${id}/verify`, {
      method: "POST",
      body: JSON.stringify({ verified_by: verifiedBy }),
    }),

  signals: (ref: string) => request<any[]>(`/api/campaigns/${ref}/signals`),

  watchRules: () => request<any[]>("/api/watch/rules"),

  setWatchRuleEnabled: (id: number, enabled: boolean) =>
    request<any>(`/api/watch/rules/${id}/enabled`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),

  watchHits: (limit = 50) => request<any[]>(`/api/watch/hits?limit=${limit}`),

  watchStatus: () => request<any>("/api/watch/status"),

  imports: () => request<any[]>("/api/imports"),

  allOutlets: () =>
    request<{ outlets: any[]; count: number; with_sourced_reach: number; reach_coverage_pct: number | null }>(
      "/api/outlets",
    ),

  exportUrls: {
    comparison: (refs: string[], atDay?: number | null) => {
      const params = new URLSearchParams();
      refs.forEach((r) => params.append("campaign", r));
      if (atDay != null) params.set("at_day", String(atDay));
      return `${API_BASE}/api/export/comparison.csv?${params}`;
    },
    table: (ref: string, what: string) =>
      `${API_BASE}/api/export/${ref}/${what}.csv`,
    briefing: (refs: string[], atDay?: number | null, format = "html") => {
      const params = new URLSearchParams();
      refs.forEach((r) => params.append("campaign", r));
      if (atDay != null) params.set("at_day", String(atDay));
      params.set("format", format);
      return `${API_BASE}/api/export/briefing?${params}`;
    },
  },
};

/* ---------------------------------------------------------------- display helpers */

/** Render a metric's value. Never invents a number, and never shows a bare dash for a refusal. */
export function formatMetricValue(metric: Metric): string {
  if (!metric.available) return "not available";
  const v = metric.value;
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "number") {
    return Number.isInteger(v) ? v.toLocaleString() : v.toFixed(2);
  }
  if (typeof v === "object") {
    const entries = Object.entries(v as Record<string, any>);
    if (!entries.length) return "—";
    return entries
      .slice(0, 3)
      .map(([k, val]) =>
        typeof val === "object" && val !== null && "articles" in val
          ? `${k} ${val.articles}`
          : `${k} ${val}`,
      )
      .join(", ") + (entries.length > 3 ? ` +${entries.length - 3}` : "");
  }
  return String(v);
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return value.slice(0, 10);
}
