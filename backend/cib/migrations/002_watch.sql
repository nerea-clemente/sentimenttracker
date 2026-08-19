-- 002_watch.sql — the trigger system. For campaigns that have not yet published, the tool's job
-- is to catch day zero.

CREATE TABLE watch_rules (
    id               INTEGER PRIMARY KEY,
    campaign_id      INTEGER REFERENCES campaigns(id) ON DELETE CASCADE,
    name             TEXT    NOT NULL,
    rule_type        TEXT    NOT NULL,
    -- keyword/phrase/byline: the text to match. domain: a hostname.
    -- rss/sitemap/gdelt_query: the feed URL or query string.
    pattern          TEXT    NOT NULL,
    -- Optional feed to poll for keyword/phrase/byline rules. NULL means "apply to every feed".
    source_url       TEXT,
    enabled          INTEGER NOT NULL DEFAULT 1,
    -- When a rule with this flag hits, the campaign's published_at is set to the hit time and
    -- daily snapshotting starts. Reserved for rules precise enough to mean "it has published".
    promotes_to_live INTEGER NOT NULL DEFAULT 0,
    last_polled_at   TEXT,
    last_error       TEXT,
    notes            TEXT,
    created_at       TEXT    NOT NULL,
    created_by       TEXT    NOT NULL,

    CHECK (rule_type IN ('keyword', 'phrase', 'byline', 'domain', 'rss', 'sitemap', 'gdelt_query')),
    CHECK (enabled IN (0, 1)),
    CHECK (promotes_to_live IN (0, 1))
);
CREATE INDEX ix_watch_rules_campaign ON watch_rules(campaign_id, enabled);

CREATE TABLE watch_hits (
    id              INTEGER PRIMARY KEY,
    rule_id         INTEGER NOT NULL REFERENCES watch_rules(id) ON DELETE CASCADE,
    occurred_at     TEXT    NOT NULL,   -- publication time of the matched item, where known
    detected_at     TEXT    NOT NULL,   -- when the poller saw it
    matched_url     TEXT    NOT NULL,
    matched_title   TEXT,
    matched_excerpt TEXT,
    raw             TEXT,               -- json: the raw feed entry, kept for provenance
    notified_at     TEXT,
    promoted        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL,

    CHECK (promoted IN (0, 1)),
    CHECK (raw IS NULL OR json_valid(raw))
);
-- A re-poll of the same feed must not re-alert on an item already seen.
CREATE UNIQUE INDEX ux_watch_hits_rule_url ON watch_hits(rule_id, matched_url);
CREATE INDEX ix_watch_hits_detected ON watch_hits(detected_at);

-- Ledger of poller runs, so "we saw nothing" is distinguishable from "the poller was not running".
CREATE TABLE watch_runs (
    id           INTEGER PRIMARY KEY,
    started_at   TEXT    NOT NULL,
    finished_at  TEXT,
    rules_polled INTEGER NOT NULL DEFAULT 0,
    hits_new     INTEGER NOT NULL DEFAULT 0,
    errors       INTEGER NOT NULL DEFAULT 0,
    detail       TEXT,
    created_by   TEXT    NOT NULL
);
