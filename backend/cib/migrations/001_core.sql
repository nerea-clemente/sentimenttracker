-- 001_core.sql — campaigns, coverage, entities, escalations, provenance.
--
-- Conventions used throughout this schema:
--   * Timestamps are ISO-8601 strings. Anything named *_at is UTC ("...Z") unless it is a
--     *date* of publication, which is stored as a local calendar date in the campaign's timezone.
--   * Every table carries created_at + created_by. Every write path records who or what wrote it.
--   * CHECK constraints encode the project's design principles so they cannot be bypassed by
--     application code: a reach figure without a source is rejected by the database itself.

CREATE TABLE campaigns (
    id                INTEGER PRIMARY KEY,
    name              TEXT    NOT NULL,
    slug              TEXT    NOT NULL UNIQUE,
    publisher_org     TEXT    NOT NULL,
    campaign_type     TEXT    NOT NULL,
    status            TEXT    NOT NULL,
    -- NULL until the campaign publishes. Day 0 of the comparison x-axis.
    published_at      TEXT,
    -- First internal awareness of the campaign, however weak. Often long before published_at.
    first_signal_at   TEXT,
    -- IANA zone used to bucket article timestamps into days. Without a fixed zone per campaign,
    -- day boundaries for international coverage are arbitrary and day_index is not comparable.
    timezone          TEXT    NOT NULL DEFAULT 'Europe/Copenhagen',
    themes            TEXT    NOT NULL DEFAULT '[]',   -- json array
    notes             TEXT,
    created_at        TEXT    NOT NULL,
    created_by        TEXT    NOT NULL,

    CHECK (campaign_type IN ('journalism', 'ngo_report', 'coalition', 'regulatory')),
    CHECK (status IN ('pre_publication', 'live', 'decaying', 'archived')),
    -- Structural guard for the pre-publication rule: a campaign is pre_publication if and only if
    -- it has no publication date. This is what stops the comparison view rendering "0 articles".
    CHECK ((status = 'pre_publication') = (published_at IS NULL)),
    CHECK (json_valid(themes))
);

CREATE TABLE outlets (
    id                           INTEGER PRIMARY KEY,
    name                         TEXT    NOT NULL,
    domain                       TEXT    UNIQUE,
    country                      TEXT,             -- ISO 3166-1 alpha-2, NULL when genuinely unknown
    language                     TEXT,             -- ISO 639-1, NULL when genuinely unknown
    tier                         TEXT    NOT NULL,
    reach_value                  INTEGER,
    reach_source                 TEXT,             -- required whenever reach_value is present
    reach_is_estimated           INTEGER NOT NULL DEFAULT 0,
    reach_estimation_method      TEXT,             -- required whenever reach_is_estimated = 1
    reach_as_of                  TEXT,
    is_known_syndication_partner INTEGER NOT NULL DEFAULT 0,
    notes                        TEXT,
    created_at                   TEXT    NOT NULL,
    created_by                   TEXT    NOT NULL,

    CHECK (tier IN ('national_general', 'national_business', 'trade', 'regional',
                    'broadcast', 'wire', 'aggregator', 'ngo', 'newsletter', 'blog')),
    -- Design principle: no invented data. A reach number must name where it came from.
    CHECK (reach_value IS NULL OR reach_source IS NOT NULL),
    CHECK (reach_is_estimated IN (0, 1)),
    CHECK (reach_is_estimated = 0 OR reach_estimation_method IS NOT NULL),
    CHECK (is_known_syndication_partner IN (0, 1))
);

CREATE TABLE imports (
    id                     INTEGER PRIMARY KEY,
    source                 TEXT    NOT NULL,
    file_name              TEXT,
    file_hash              TEXT,            -- sha256 of the source file; makes re-imports detectable
    imported_at            TEXT    NOT NULL,
    row_count              INTEGER NOT NULL DEFAULT 0,
    rows_inserted          INTEGER NOT NULL DEFAULT 0,
    rows_skipped_duplicate INTEGER NOT NULL DEFAULT 0,
    rows_rejected          INTEGER NOT NULL DEFAULT 0,
    mapping                TEXT,            -- json: the column mapping actually applied
    notes                  TEXT,
    created_by             TEXT    NOT NULL,

    CHECK (source IN ('infomedia', 'factiva', 'nexis', 'gdelt', 'mediacloud', 'rss', 'manual')),
    CHECK (mapping IS NULL OR json_valid(mapping))
);

CREATE TABLE clusters (
    id                       INTEGER PRIMARY KEY,
    campaign_id              INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    representative_article_id INTEGER,        -- FK added logically; articles is created after this table
    method                   TEXT    NOT NULL,
    member_count             INTEGER NOT NULL DEFAULT 0,
    created_at               TEXT    NOT NULL,
    created_by               TEXT    NOT NULL,

    CHECK (method IN ('hash', 'title_similarity', 'shingle', 'manual'))
);

CREATE TABLE articles (
    id              INTEGER PRIMARY KEY,
    campaign_id     INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    outlet_id       INTEGER NOT NULL REFERENCES outlets(id),
    url             TEXT,
    headline        TEXT    NOT NULL,
    published_at    TEXT    NOT NULL,
    -- Cached whole-day offset from the campaign's published_at, in the campaign's timezone.
    -- Recomputable with `cib recompute day-index`; may be negative for pre-publication leaks.
    day_index       INTEGER,
    language        TEXT,
    country         TEXT,
    byline          TEXT,
    body_text       TEXT,
    body_hash       TEXT,                    -- sha256 of the normalised body; NULL when no body captured
    word_count      INTEGER,
    cluster_id      INTEGER REFERENCES clusters(id) ON DELETE SET NULL,
    -- 1 for the representative of a syndication cluster and for any unclustered article.
    -- Duplicates are marked, never deleted.
    is_original     INTEGER NOT NULL DEFAULT 1,
    import_id       INTEGER NOT NULL REFERENCES imports(id),
    row_fingerprint TEXT,                    -- sha256 of the normalised source row
    retrieved_at    TEXT,
    created_at      TEXT    NOT NULL,
    created_by      TEXT    NOT NULL,

    CHECK (is_original IN (0, 1))
);

-- Idempotent re-import. A URL identifies an article within a campaign; where the source export
-- carries no URL we fall back to outlet + body hash + publication timestamp.
CREATE UNIQUE INDEX ux_articles_campaign_url
    ON articles(campaign_id, url) WHERE url IS NOT NULL;
CREATE UNIQUE INDEX ux_articles_campaign_nourl
    ON articles(campaign_id, outlet_id, body_hash, published_at) WHERE url IS NULL;

CREATE INDEX ix_articles_campaign_day    ON articles(campaign_id, day_index);
CREATE INDEX ix_articles_campaign_outlet ON articles(campaign_id, outlet_id);
CREATE INDEX ix_articles_cluster         ON articles(cluster_id);
CREATE INDEX ix_articles_body_hash       ON articles(campaign_id, body_hash);
CREATE INDEX ix_articles_import          ON articles(import_id);

-- Cluster membership is a table rather than only articles.cluster_id so that the *evidence* for
-- each membership decision (which method matched, at what similarity) is auditable and reversible.
CREATE TABLE cluster_members (
    cluster_id INTEGER NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    method     TEXT    NOT NULL,
    similarity REAL,
    matched_against_article_id INTEGER REFERENCES articles(id),
    created_at TEXT    NOT NULL,
    created_by TEXT    NOT NULL,

    PRIMARY KEY (cluster_id, article_id),
    CHECK (method IN ('hash', 'title_similarity', 'shingle', 'manual'))
);
CREATE INDEX ix_cluster_members_article ON cluster_members(article_id);

CREATE TABLE entities (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    type       TEXT    NOT NULL,
    aliases    TEXT    NOT NULL DEFAULT '[]',   -- json array of surface forms
    notes      TEXT,
    created_at TEXT    NOT NULL,
    created_by TEXT    NOT NULL,

    CHECK (type IN ('own_company', 'competitor', 'ngo', 'certifier',
                    'customer', 'supplier', 'regulator')),
    CHECK (json_valid(aliases))
);
CREATE UNIQUE INDEX ux_entities_name ON entities(name);

CREATE TABLE mentions (
    id               INTEGER PRIMARY KEY,
    article_id       INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    entity_id        INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    role             TEXT    NOT NULL,
    -- Never optional: a mention you cannot quote back is a mention you cannot defend.
    evidence_sentence TEXT   NOT NULL,
    char_offset      INTEGER,
    confidence       REAL,
    classified_by    TEXT    NOT NULL,
    created_at       TEXT    NOT NULL,
    created_by       TEXT    NOT NULL,

    CHECK (role IN ('subject', 'named_supplier', 'named_buyer',
                    'quoted_response', 'passing_reference')),
    CHECK (classified_by IN ('rule', 'llm', 'human')),
    -- An LLM assignment without a confidence value cannot be filtered or reviewed, so it is rejected.
    CHECK (classified_by <> 'llm' OR confidence IS NOT NULL)
);
CREATE INDEX ix_mentions_article ON mentions(article_id);
CREATE INDEX ix_mentions_entity  ON mentions(entity_id);
CREATE UNIQUE INDEX ux_mentions_dedupe ON mentions(article_id, entity_id, role, char_offset);

CREATE TABLE escalations (
    id              INTEGER PRIMARY KEY,
    campaign_id     INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    occurred_at     TEXT    NOT NULL,
    escalation_type TEXT    NOT NULL,
    actor_name      TEXT    NOT NULL,
    actor_type      TEXT,
    description     TEXT    NOT NULL,
    -- Required: an escalation is a claim about the outside world and must cite it.
    source_url      TEXT    NOT NULL,
    -- 1 statement of concern | 2 formal query or parliamentary question |
    -- 3 buyer, retailer or certifier action | 4 regulatory investigation opened |
    -- 5 binding regulatory or customs measure, or litigation filed
    severity        INTEGER NOT NULL,
    verified_by     TEXT,
    verified_at     TEXT,
    created_at      TEXT    NOT NULL,
    created_by      TEXT    NOT NULL,

    CHECK (escalation_type IN ('legal_petition', 'regulatory_action', 'customs_measure',
                               'retailer_statement', 'buyer_statement', 'parliamentary_question',
                               'certifier_response', 'company_response', 'other')),
    CHECK (severity BETWEEN 1 AND 5),
    CHECK (length(trim(source_url)) > 0)
);
CREATE INDEX ix_escalations_campaign ON escalations(campaign_id, occurred_at);

CREATE TABLE inbound_signals (
    id          INTEGER PRIMARY KEY,
    campaign_id INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
    occurred_at TEXT    NOT NULL,
    channel     TEXT    NOT NULL,
    summary     TEXT    NOT NULL,
    source_ref  TEXT,                       -- internal reference: ticket, email subject, tender id
    logged_by   TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    created_by  TEXT    NOT NULL,

    CHECK (channel IN ('journalist', 'customer', 'tender', 'investor', 'employee', 'other'))
);
CREATE INDEX ix_inbound_campaign ON inbound_signals(campaign_id, occurred_at);

-- Tone is a *secondary* label, deliberately held in its own table rather than as a column on
-- articles. Coverage of labour or environmental abuse is negative by construction, so a tone score
-- does not discriminate between a trade story and a crisis. No metrics function reads this table
-- and no comparison endpoint exposes it.
CREATE TABLE article_tone (
    article_id        INTEGER PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
    label             TEXT    NOT NULL,
    evidence_sentence TEXT    NOT NULL,
    confidence        REAL,
    classified_by     TEXT    NOT NULL,
    created_at        TEXT    NOT NULL,
    created_by        TEXT    NOT NULL,

    CHECK (label IN ('negative', 'mixed', 'neutral', 'positive')),
    CHECK (classified_by IN ('rule', 'llm', 'human')),
    CHECK (classified_by <> 'llm' OR confidence IS NOT NULL)
);

CREATE TABLE metrics_daily (
    campaign_id              INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    date                     TEXT    NOT NULL,   -- calendar date in the campaign's timezone
    day_index                INTEGER NOT NULL,
    article_count            INTEGER NOT NULL,
    cumulative_articles      INTEGER NOT NULL,
    unique_outlets           INTEGER NOT NULL,
    cumulative_unique_outlets INTEGER NOT NULL,
    countries                INTEGER NOT NULL,
    languages                INTEGER NOT NULL,
    own_company_mentions     INTEGER NOT NULL,
    computed_at              TEXT    NOT NULL,

    PRIMARY KEY (campaign_id, date)
);
CREATE INDEX ix_metrics_daily_dayindex ON metrics_daily(campaign_id, day_index);
