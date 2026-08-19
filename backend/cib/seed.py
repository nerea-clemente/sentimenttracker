"""Seed data.

Three campaign records, two archived and one pre-publication, plus the entity and watch-rule
scaffolding around them.

Two rules govern what is in here:

  1. **No invented article data.** The two archived campaigns are created empty. Their footprints
     stay unmeasured until real Infomedia / Factiva / Nexis exports are imported. The empty state
     says so explicitly rather than rendering as zero coverage.
  2. **No invented identities.** Names, publishers, domains and dates are placeholders marked
     TODO. Watch rules are created *disabled*, because a rule pointing at a placeholder domain
     would poll nothing and quietly look healthy. Fill the real values in with
     `cib campaign set` and `cib watch enable`.
"""

from __future__ import annotations

import sqlite3

from .repo import campaigns as campaign_repo
from .repo import entities as entity_repo
from .watch import rules as rule_repo

TODO = "TODO: replace this placeholder with the real value before relying on this record."

CAMPAIGNS = [
    {
        "name": "TODO — 2019 Danish NGO investigation into West African fishmeal",
        "slug": "seed-2019-danish-ngo-west-african-fishmeal",
        "publisher_org": "TODO — publishing NGO",
        "campaign_type": "ngo_report",
        "status": "archived",
        "published_at": "2019-01-01T00:00:00",
        "timezone": "Europe/Copenhagen",
        "themes": ["fishmeal", "west_africa", "food_security", "environment"],
        "notes": (
            "Retrospective baseline #1. " + TODO + " The publication date above is a placeholder "
            "standing in for the real one; correct it with `cib campaign set-published` before "
            "importing coverage, because every day_index is measured from it.\n\n"
            "No article data is seeded. Import the real Infomedia export with:\n"
            "  cib import infomedia --campaign seed-2019-danish-ngo-west-african-fishmeal "
            "--file data/imports/<export>.csv"
        ),
    },
    {
        "name": "TODO — NGO/coalition campaign on fishmeal and aquaculture sourcing",
        "slug": "seed-ngo-coalition-fishmeal-aquaculture-sourcing",
        "publisher_org": "TODO — coalition or lead NGO",
        "campaign_type": "coalition",
        "status": "archived",
        "published_at": "2020-01-01T00:00:00",
        "timezone": "Europe/Copenhagen",
        "themes": ["fishmeal", "aquaculture", "sourcing", "certification"],
        "notes": (
            "Retrospective baseline #2. " + TODO + " Placeholder publication date; correct it "
            "before importing coverage.\n\n"
            "No article data is seeded. Import real Factiva or Nexis exports with:\n"
            "  cib import factiva --campaign seed-ngo-coalition-fishmeal-aquaculture-sourcing "
            "--file data/imports/<export>.rtf"
        ),
    },
    {
        "name": "TODO — forthcoming journalism investigation into global fishmeal "
                "and aquaculture supply chains",
        "slug": "seed-forthcoming-fishmeal-supply-chain-investigation",
        "publisher_org": "TODO — publishing outlet or consortium",
        "campaign_type": "journalism",
        "status": "pre_publication",
        "published_at": None,
        "timezone": "Europe/Copenhagen",
        "themes": ["fishmeal", "aquaculture", "supply_chain", "labour", "investigation"],
        "notes": (
            "Pre-publication. This campaign deliberately has no publication date, so the "
            "comparison view refuses to render footprint metrics for it and shows publisher "
            "precedent and logged signals instead.\n\n" + TODO + "\n\n"
            "Before this is useful: set the real publisher, then point the watch rules at the "
            "publisher's real site and newsletter and enable them:\n"
            "  cib watch list\n"
            "  cib watch enable <rule-id>"
        ),
    },
]

# Pre-publication signals for campaign 3. The spec names two: congressional testimony and the
# trade press picking that testimony up. Dates and detail are placeholders — an inbound signal is
# evidence, and evidence with an invented date is worse than none.
INBOUND_SIGNALS = [
    {
        "channel": "other",
        "occurred_at": "1970-01-01T00:00:00",
        "summary": (
            "TODO — congressional testimony referencing fishmeal and aquaculture supply chains. "
            "Replace the placeholder date and add the hearing reference before this is cited."
        ),
        "source_ref": "TODO — hearing reference or transcript URL",
    },
    {
        "channel": "journalist",
        "occurred_at": "1970-01-01T00:00:00",
        "summary": (
            "TODO — trade-press pickup of the congressional testimony. Replace the placeholder "
            "date and add the article reference before this is cited."
        ),
        "source_ref": "TODO — trade press article URL",
    },
]

# Watch rules for campaign 3, created DISABLED. A rule pointing at a placeholder domain polls
# nothing while looking healthy, which is exactly the failure this tool exists to prevent.
WATCH_RULES = [
    {
        "name": "TODO — publisher site RSS",
        "rule_type": "rss",
        "pattern": "https://example.invalid/feed",
        "promotes_to_live": True,
        "notes": "Replace the URL with the publisher's real feed, then enable. "
                 "promotes_to_live is on: a hit here sets the campaign's publication date.",
    },
    {
        "name": "TODO — publisher newsletter feed",
        "rule_type": "rss",
        "pattern": "https://example.invalid/newsletter.xml",
        "promotes_to_live": False,
        "notes": "Replace the URL with the publisher's real newsletter feed, then enable.",
    },
    {
        "name": "TODO — publisher news sitemap",
        "rule_type": "sitemap",
        "pattern": "https://example.invalid/news-sitemap.xml",
        "promotes_to_live": True,
        "notes": "Replace with the publisher's Google News sitemap, then enable.",
    },
    {
        "name": "Keyword: fishmeal + aquaculture",
        "rule_type": "keyword",
        "pattern": "fishmeal aquaculture",
        "promotes_to_live": False,
        "notes": "Content rule. Applied to whichever feeds are enabled for this campaign.",
    },
    {
        "name": "TODO — investigating journalist byline",
        "rule_type": "byline",
        "pattern": "TODO REPLACE WITH BYLINE",
        "promotes_to_live": False,
        "notes": "Replace with the reporter's name once known, then enable.",
    },
]

ENTITIES = [
    {
        "name": "TODO — our company",
        "type": "own_company",
        "aliases": [],
        "notes": "The entity every exposure metric is measured for. Replace the name and add the "
                 "legal entity names, trading names and common misspellings as aliases. Aliases "
                 "shorter than three characters are ignored by the matcher.",
    },
]


def run(conn: sqlite3.Connection, force: bool = False) -> dict:
    """Create the seed records. Idempotent: existing slugs are left alone unless force is set."""
    created = {"campaigns": [], "entities": [], "signals": 0, "watch_rules": 0, "precedents": 0}

    for spec in CAMPAIGNS:
        existing = campaign_repo.get_by_slug(conn, spec["slug"])
        if existing and not force:
            continue
        if existing and force:
            conn.execute("DELETE FROM campaigns WHERE id = ?", (existing.id,))
        campaign_repo.create(
            conn,
            name=spec["name"], slug=spec["slug"], publisher_org=spec["publisher_org"],
            campaign_type=spec["campaign_type"], status=spec["status"],
            published_at=spec["published_at"], timezone=spec["timezone"],
            themes=spec["themes"], notes=spec["notes"],
        )
        created["campaigns"].append(spec["slug"])

    for spec in ENTITIES:
        if entity_repo.get_by_name(conn, spec["name"]) is None:
            entity_repo.create(conn, name=spec["name"], type=spec["type"],
                               aliases=spec["aliases"], notes=spec["notes"])
            created["entities"].append(spec["name"])

    pre_pub = campaign_repo.get_by_slug(conn, CAMPAIGNS[2]["slug"])
    if pre_pub is not None:
        from .repo import events as event_repo

        existing_signals = event_repo.inbound_signals_for(conn, pre_pub.id)
        if not existing_signals:
            for signal in INBOUND_SIGNALS:
                event_repo.add_inbound_signal(
                    conn, campaign_id=pre_pub.id, occurred_at=signal["occurred_at"],
                    channel=signal["channel"], summary=signal["summary"],
                    source_ref=signal["source_ref"], logged_by="seed",
                )
                created["signals"] += 1

        if not rule_repo.list_all(conn, campaign_id=pre_pub.id):
            for rule in WATCH_RULES:
                rule_repo.create(
                    conn, name=rule["name"], rule_type=rule["rule_type"],
                    pattern=rule["pattern"], campaign_id=pre_pub.id,
                    enabled=False,   # never poll a placeholder domain
                    promotes_to_live=rule["promotes_to_live"], notes=rule["notes"],
                )
                created["watch_rules"] += 1

        # Link the two archived campaigns as precedent, so the pre-publication view has something
        # measured to show once their exports are imported.
        for slug in (CAMPAIGNS[0]["slug"], CAMPAIGNS[1]["slug"]):
            precedent = campaign_repo.get_by_slug(conn, slug)
            if precedent is None:
                continue
            already = any(int(r["precedent_campaign_id"]) == precedent.id
                          for r in campaign_repo.precedents(conn, pre_pub.id))
            if already:
                continue
            campaign_repo.add_precedent(
                conn, pre_pub.id, precedent.id,
                rationale=("Seeded link: same sector and theme, used as a retrospective baseline. "
                           + TODO),
            )
            created["precedents"] += 1

    return created
