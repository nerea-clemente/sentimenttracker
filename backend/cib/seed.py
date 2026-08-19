"""Seed data — the real campaigns, entities and signals for BioMar.

Every identity here is traceable to a named source, recorded in the record's own notes. Nothing
was filled in from recollection: where a fact could not be verified it is left unset and reported
by `cib doctor` rather than guessed at.

Two things are deliberately *not* seeded:

  * **Article data.** The archive exports are licensed and must be imported by hand. The campaigns
    below are created empty and the empty state says so, rather than rendering as low coverage.
  * **Days that are not known.** The Danwatch investigation is recorded as October 2019 with
    `published_at_precision = 'month'`, because that is how precisely the date could be confirmed.
    Every day-aligned figure then carries that error bar until someone pins the day.
"""

from __future__ import annotations

import sqlite3

from .repo import campaigns as campaign_repo
from .repo import entities as entity_repo
from .watch import rules as rule_repo

# Sources consulted when compiling this file. Cited in the notes of the records they support.
SOURCES = {
    "danwatch_series": "https://danwatch.dk/serie/de-fisk-du-ikke-ved-du-spiser/",
    "danwatch_companies": "https://danwatch.dk/det-siger-virksomhederne-om-import-af-fisk-fra-vestafrika/",
    "danwatch_escalation": "https://danwatch.dk/fiskemels-afsloeringer-tages-op-i-eu-og-folketinget/",
    "danwatch_transparency": "https://danwatch.dk/eksperter-kraever-stoerre-gennemsigtighed-med-import-af-fiskemel-og-olie-fra-vestafrika/",
    "danwatch_companies_en": "https://danwatch.dk/en/they-buy-fish-meal-and-fish-oil-from-west-africa/",
    "danwatch_forbandet": "https://danwatch.dk/forbandet-fiskemel/",
    "amnesty_prize": "https://amnesty.dk/amnestys-mediepris-2025-tildeles-artikelserier-om-skaemmende-forhold-paa-bosteder-og-fiskemels-betydning-for-migrationen-til-europa/",
    "changing_markets": "https://changingmarkets.org/report/fishing-for-catastrophe/",
    "changing_markets_pdf": "https://changingmarkets.org/wp-content/uploads/2023/10/CM-WEB-FINAL-FISHING-FOR-CATASTROPHE-2019.pdf",
    "feed_reaction": "https://www.agtechnavigator.com/Article/2019/10/21/feed-makers-retailers-react-to-claims-in-fishmeal-report/",
    "outlaw_ocean_testimony": "https://theoutlawocean.substack.com/p/the-outlaw-ocean-testifies-before",
    "seafoodsource_teaser": "https://www.seafoodsource.com/news/aquaculture/outlaw-ocean-project-teases-new-aquaculture-investigation-during-congressional-hearing",
    "cecc_hearing": "https://www.cecc.gov/media-center/press-releases/forced-labor-in-china%E2%80%99s-seafood-industry-explored-at-hearing",
    "cecc_record": "https://www.govinfo.gov/content/pkg/CHRG-118jhrg54082/pdf/CHRG-118jhrg54082.pdf",
}

CAMPAIGNS = [
    {
        "name": "Danwatch: De fisk du ikke ved du spiser",
        "slug": "danwatch-2019-west-african-fishmeal",
        "publisher_org": "Danwatch",
        "campaign_type": "journalism",
        "status": "archived",
        # Confirmed to the month only. See published_at_precision below — this is not a guess
        # dressed as a date, and every day-aligned figure says so.
        "published_at": "2019-10-01T00:00:00",
        "published_at_precision": "month",
        "timezone": "Europe/Copenhagen",
        "themes": ["fishmeal", "west_africa", "food_security", "supply_chain"],
        "notes": (
            "Danish investigative outlet Danwatch's series on fishmeal and fish oil imported from "
            "West Africa. In October 2019 Danwatch put questions to six Danish companies buying "
            "fishmeal and fish oil from the region about how they avoid affecting West African "
            "food security. BioMar was among the companies named, alongside Pelagia, Triple Nine "
            "Group, FF Skagen, Aller Aqua and ED&F Man.\n\n"
            "DATE PRECISION: confirmed as October 2019; the exact publication day was not "
            "established. published_at is set to the 1st with precision='month', so every "
            "day-aligned figure carries that error bar. Pin it with `cib campaign set-published` "
            "once confirmed against the source.\n\n"
            f"Sources: {SOURCES['danwatch_series']} · {SOURCES['danwatch_companies']} · "
            f"English version: {SOURCES['danwatch_companies_en']}\n\n"
            "KNOWN ESCALATION, NOT YET LOGGED: Danwatch reported that the fishmeal revelations "
            "were taken up in the EU and in the Folketing. On the severity anchors that is a 2 "
            "(formal query or parliamentary question), and it is the first link in this "
            "campaign's escalation chain. It is not logged because the date could not be "
            "confirmed and an escalation with a guessed date is not evidence. Log it once dated:\n"
            "  cib escalation add --campaign danwatch-2019-west-african-fishmeal \\\n"
            "    --type parliamentary_question --actor 'Folketinget' --severity 2 \\\n"
            f"    --occurred-at <date> --source-url {SOURCES['danwatch_escalation']} \\\n"
            "    --description 'Fishmeal revelations raised in the EU and the Danish parliament'\n"
            f"A related follow-up on transparency demands: {SOURCES['danwatch_transparency']}\n\n"
            "No article data seeded — import the Infomedia export:\n"
            "  cib import infomedia --campaign danwatch-2019-west-african-fishmeal --file <export>"
        ),
    },
    {
        "name": "Changing Markets Foundation: Fishing for Catastrophe",
        "slug": "changing-markets-2019-fishing-for-catastrophe",
        "publisher_org": "Changing Markets Foundation",
        "campaign_type": "ngo_report",
        "status": "archived",
        "published_at": "2019-10-15T00:00:00",
        "published_at_precision": "day",
        "timezone": "Europe/London",
        "themes": ["fishmeal", "fish_oil", "aquaculture", "sourcing", "certification"],
        "notes": (
            "NGO report mapping fishmeal and fish oil (FMFO) supply chains from fishery to fork, "
            "based on field investigations in India, Vietnam and The Gambia in mid-2019. It links "
            "unsustainable and in places illegal FMFO sourcing to major European aquafeed "
            "companies and retailers. BioMar is named as a buyer of marine ingredients from West "
            "Africa and publicly responded, saying the authors misconstrued certain data. "
            "Skretting also responded. Trade press covered the reaction on 21 October 2019.\n\n"
            f"Sources: {SOURCES['changing_markets']} · {SOURCES['changing_markets_pdf']} · "
            f"{SOURCES['feed_reaction']}\n\n"
            "No article data seeded — import the Factiva or Nexis export:\n"
            "  cib import factiva --campaign changing-markets-2019-fishing-for-catastrophe "
            "--file <export>"
        ),
    },
    {
        "name": "The Outlaw Ocean Project: Food for Feed",
        "slug": "outlaw-ocean-food-for-feed",
        "publisher_org": "The Outlaw Ocean Project",
        "campaign_type": "journalism",
        "status": "pre_publication",
        "published_at": None,
        "timezone": "America/New_York",
        "themes": [
            "fishmeal", "aquaculture", "supply_chain", "forced_labour",
            "west_africa", "china", "investigation",
        ],
        "notes": (
            "A cross-border investigation into the global fishmeal and aquaculture industries by "
            "The Outlaw Ocean Project, reported by more than two dozen journalists across as many "
            "countries, covering roughly 1,400 fishmeal plants, the vessels supplying them and "
            "where the resulting seafood ends up. Reported findings include fish farms in "
            "Xinjiang and Tibet, forced labour in Russia's Far East, the industry's role in "
            "Western Sahara, and fish diverted from local consumption into fishmeal plants in "
            "The Gambia and Mauritania.\n\n"
            "STATUS: recorded as pre_publication. Preliminary findings were presented to a US "
            "congressional hearing in October 2023 and picked up by the trade press, but full "
            "publication could not be confirmed. VERIFY THIS before relying on it: if the "
            "investigation has published, set the date with\n"
            "  cib campaign set-published outlaw-ocean-food-for-feed <date> --status live\n"
            "which also recomputes day_index across any coverage already imported.\n\n"
            f"Sources: {SOURCES['outlaw_ocean_testimony']} · {SOURCES['seafoodsource_teaser']}"
        ),
    },
    {
        "name": "Danwatch: Forbandet fiskemel",
        "slug": "danwatch-2024-forbandet-fiskemel",
        "publisher_org": "Danwatch",
        "campaign_type": "journalism",
        "status": "archived",
        "published_at": "2024-01-01T00:00:00",
        "published_at_precision": "year",
        "timezone": "Europe/Copenhagen",
        "themes": ["fishmeal", "west_africa", "migration", "food_security"],
        "notes": (
            "Danwatch's later fishmeal series, reported by Oscar Rothstein from Senegal and "
            "Mauritania, on how fishmeal and fish oil production bought in quantity by Danish "
            "companies contributes to migration towards the Canary Islands. Reporting was carried "
            "out in autumn 2024. The series won Amnesty International Denmark's Media Prize "
            "2025.\n\n"
            "DATE PRECISION: only the year could be confirmed, so published_at is 1 January with "
            "precision='year' and every day-aligned figure carries that error bar. This campaign "
            "is the strongest available precedent for a Danwatch investigation, so pinning its "
            "real date is worth doing early.\n\n"
            f"Sources: {SOURCES['danwatch_forbandet']} · {SOURCES['amnesty_prize']}"
        ),
    },
]

# Pre-publication signals for the Outlaw Ocean investigation. The spec calls for exactly these
# two: the congressional testimony and the trade-press pickup of it.
INBOUND_SIGNALS = [
    {
        "channel": "other",
        "occurred_at": "2023-10-26T00:00:00",
        "summary": (
            "Congressional testimony: The Outlaw Ocean Project presented preliminary findings "
            "from its fishmeal and aquaculture investigation to the Congressional-Executive "
            "Commission on China hearing on forced labour in China's seafood industry. VERIFY the "
            "hearing date against the record before citing."
        ),
        "source_ref": SOURCES["cecc_hearing"],
    },
    {
        "channel": "journalist",
        "occurred_at": "2023-10-26T00:00:00",
        "summary": (
            "Trade-press pickup: SeafoodSource reported that the Outlaw Ocean Project had teased "
            "a new aquaculture investigation during the congressional hearing. Date assumed to be "
            "the hearing date — VERIFY and correct."
        ),
        "source_ref": SOURCES["seafoodsource_teaser"],
    },
]

# Watch rules for the pre-publication campaign. Feed URLs that could be confirmed are enabled;
# anything unconfirmed stays disabled, because a rule pointing at a guessed URL polls nothing
# while looking healthy.
WATCH_RULES = [
    {
        "name": "The Outlaw Ocean Project — Substack feed",
        "rule_type": "rss",
        "pattern": "https://theoutlawocean.substack.com/feed",
        "enabled": True,
        "promotes_to_live": False,
        "notes": (
            "Substack publishes an RSS feed at /feed. Detection only — not marked "
            "promotes_to_live, because the newsletter carries plenty of posts that are not this "
            "investigation and one of them should not set the campaign's day zero."
        ),
    },
    {
        "name": "Keyword: fishmeal + aquaculture",
        "rule_type": "keyword",
        "pattern": "fishmeal aquaculture",
        "enabled": True,
        "promotes_to_live": False,
        "notes": "Content rule, applied to whichever feeds are enabled for this campaign.",
    },
    {
        "name": "Byline: Ian Urbina",
        "rule_type": "byline",
        "pattern": "Ian Urbina",
        "enabled": True,
        "promotes_to_live": False,
        "notes": (
            "The Outlaw Ocean Project's founder and lead reporter. Confirm the actual byline on "
            "the investigation when it lands — it may be a team credit."
        ),
    },
    {
        "name": "GDELT: fishmeal and aquaculture supply chains",
        "rule_type": "gdelt_query",
        "pattern": '"fishmeal" ("aquaculture" OR "fish feed")',
        "enabled": True,
        "promotes_to_live": False,
        "notes": (
            "Automated coverage ingest via `cib ingest`. Needs no API key. Gives cross-country "
            "volume within minutes of a story breaking, but no article body, so these rows cannot "
            "be scanned for entity mentions."
        ),
    },
    {
        "name": "TODO — publisher news sitemap",
        "rule_type": "sitemap",
        "pattern": "https://theoutlawocean.com/sitemap.xml",
        "enabled": False,
        "promotes_to_live": True,
        "notes": (
            "DISABLED: the sitemap URL is assumed, not confirmed. Verify it resolves, then "
            "enable. This is the rule marked promotes_to_live — a hit sets the campaign's "
            "publication date — so it must be precise enough to mean 'it has published'."
        ),
    },
]

# Own company and peers, taken from the existing mediatracker configuration rather than inferred.
ENTITIES = [
    {
        "name": "BioMar",
        "type": "own_company",
        "aliases": ["BioMar Group", "BioMar A/S", "BioMar Group A/S", "Biomar"],
        "notes": (
            "The entity every exposure metric is measured for. Aliases cover the group and legal "
            "entity names; add trading names and common misspellings as they turn up in imports. "
            "Aliases under three characters are ignored by the matcher by design.\n"
            "Source: the company tracked by nerea-clemente/mediatracker (PRIMARY_KEYWORD)."
        ),
    },
    {
        "name": "Schouw & Co",
        "type": "competitor",
        "aliases": ["Schouw", "Aktieselskabet Schouw & Co"],
        "notes": (
            "BioMar's parent company. There is no 'parent' entity type, and it must NOT be typed "
            "own_company: two own_company entities make the exposure metrics refuse rather than "
            "measure the wrong one. Typed competitor so coverage naming Schouw is still tracked; "
            "retype or remove if that distorts the peer comparison.\n"
            "Source: mediatracker PARENT_KEYWORD — Schouw articles roll up under BioMar there."
        ),
    },
    {
        "name": "Skretting",
        "type": "competitor",
        "aliases": ["Skretting AS", "Nutreco"],
        "notes": "Peer aquafeed producer. Source: mediatracker COMPETITOR_QUERIES.",
    },
    {
        "name": "Cargill Aqua Nutrition",
        "type": "competitor",
        "aliases": ["Cargill Aqua", "EWOS", "Ewos"],
        "notes": (
            "Peer aquafeed producer. Source: mediatracker COMPETITOR_QUERIES, which notes that "
            "'Ewos' alone also matches an Australian street name — worth remembering if mention "
            "counts look wrong."
        ),
    },
    {
        "name": "Aller Aqua",
        "type": "competitor",
        "aliases": ["Aller Aqua A/S", "Aller Aqua Group"],
        "notes": (
            "Danish aquafeed producer, named alongside BioMar among the six companies Danwatch "
            "questioned in 2019. Source: " + SOURCES["danwatch_companies"]
        ),
    },
    {
        "name": "TripleNine",
        "type": "supplier",
        "aliases": ["Triple Nine", "Triple Nine Group", "TripleNine Group"],
        "notes": (
            "Danish fishmeal and fish oil producer named in the Danwatch investigation. Source: "
            + SOURCES["danwatch_companies"]
        ),
    },
    {
        "name": "FF Skagen",
        "type": "supplier",
        "aliases": ["FF Skagen A/S"],
        "notes": (
            "Danish fishmeal and fish oil producer named in the Danwatch investigation. Source: "
            + SOURCES["danwatch_companies"]
        ),
    },
    {
        "name": "Pelagia",
        "type": "supplier",
        "aliases": ["Pelagia AS"],
        "notes": (
            "Fishmeal and fish oil producer named in the Danwatch investigation. Source: "
            + SOURCES["danwatch_companies"]
        ),
    },
    {
        "name": "MarinTrust",
        "type": "certifier",
        "aliases": ["IFFO RS", "Marin Trust"],
        "notes": (
            "Certification scheme for marine ingredients. Certifier responses are a stage in the "
            "escalation chain, so mentions of it are worth tracking."
        ),
    },
    {
        "name": "Changing Markets Foundation",
        "type": "ngo",
        "aliases": ["Changing Markets"],
        "notes": "Publisher of Fishing for Catastrophe.",
    },
    {
        "name": "Danwatch",
        "type": "ngo",
        "aliases": [],
        "notes": "Danish investigative outlet; publisher of two fishmeal investigations tracked here.",
    },
]

# Which past campaigns inform the pre-publication assessment of the forthcoming one.
PRECEDENTS = [
    ("outlaw-ocean-food-for-feed", "danwatch-2019-west-african-fishmeal",
     "Same theme and the same named companies, including BioMar. Measures what a fishmeal "
     "investigation naming BioMar actually produced in Danish coverage."),
    ("outlaw-ocean-food-for-feed", "changing-markets-2019-fishing-for-catastrophe",
     "Closest comparator for international reach: an NGO report on the same supply chain that "
     "named BioMar and drew a public company response."),
    ("outlaw-ocean-food-for-feed", "danwatch-2024-forbandet-fiskemel",
     "The most recent investigation into the same supply chain, and the best guide to how this "
     "story now travels."),
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
            published_at=spec["published_at"],
            published_at_precision=spec.get("published_at_precision", "day"),
            timezone=spec["timezone"], themes=spec["themes"], notes=spec["notes"],
        )
        created["campaigns"].append(spec["slug"])

    for spec in ENTITIES:
        if entity_repo.get_by_name(conn, spec["name"]) is None:
            entity_repo.create(conn, name=spec["name"], type=spec["type"],
                               aliases=spec["aliases"], notes=spec["notes"])
            created["entities"].append(spec["name"])

    pre_pub = campaign_repo.get_by_slug(conn, "outlaw-ocean-food-for-feed")
    if pre_pub is not None:
        from .repo import events as event_repo

        if not event_repo.inbound_signals_for(conn, pre_pub.id):
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
                    enabled=rule["enabled"], promotes_to_live=rule["promotes_to_live"],
                    notes=rule["notes"],
                )
                created["watch_rules"] += 1

    for campaign_slug, precedent_slug, rationale in PRECEDENTS:
        campaign = campaign_repo.get_by_slug(conn, campaign_slug)
        precedent = campaign_repo.get_by_slug(conn, precedent_slug)
        if campaign is None or precedent is None:
            continue
        already = any(int(r["precedent_campaign_id"]) == precedent.id
                      for r in campaign_repo.precedents(conn, campaign.id))
        if already:
            continue
        campaign_repo.add_precedent(conn, campaign.id, precedent.id, rationale=rationale)
        created["precedents"] += 1

    return created
