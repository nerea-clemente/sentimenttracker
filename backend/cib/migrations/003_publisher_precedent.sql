-- 003_publisher_precedent.sql — publisher precedent for pre-publication campaigns.
--
-- A campaign with no publication date cannot have a footprint. What it can have is the measured
-- footprint of the same publisher's previous investigations. Those precedents are ordinary
-- campaign rows; this table records the deliberate, attributed judgement that one is a precedent
-- for another, rather than inferring it from a string match on publisher_org.

CREATE TABLE publisher_precedents (
    id                     INTEGER PRIMARY KEY,
    campaign_id            INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    precedent_campaign_id  INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    rationale              TEXT    NOT NULL,
    created_at             TEXT    NOT NULL,
    created_by             TEXT    NOT NULL,

    UNIQUE (campaign_id, precedent_campaign_id),
    CHECK (campaign_id <> precedent_campaign_id)
);
