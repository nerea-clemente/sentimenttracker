"""Ingestion: idempotency, provenance and the archive-export mappers."""

from __future__ import annotations

import pytest

from cib.db import query
from cib.ingest import csv_generic, factiva, nexis
from cib.ingest.base import normalise_row


def test_reimporting_the_same_file_creates_no_duplicates(conn, campaign_id, sample_csv):
    """Acceptance criterion: re-importing the same export must not duplicate articles."""
    first = csv_generic.import_file(conn, campaign_ref=campaign_id, path=sample_csv)
    assert first.inserted == 12

    second = csv_generic.import_file(conn, campaign_ref=campaign_id, path=sample_csv)
    assert second.inserted == 0
    assert second.skipped_duplicate == 12
    assert second.already_imported_as == first.import_id

    total = query(conn, "SELECT COUNT(*) AS n FROM articles WHERE campaign_id = ?", (campaign_id,))
    assert int(total[0]["n"]) == 12


def test_a_reimport_still_records_a_ledger_entry(conn, campaign_id, sample_csv):
    """The ledger is an audit trail of what was attempted, not only of what changed."""
    csv_generic.import_file(conn, campaign_ref=campaign_id, path=sample_csv)
    csv_generic.import_file(conn, campaign_ref=campaign_id, path=sample_csv)

    ledger = query(conn, "SELECT * FROM imports ORDER BY id")
    assert len(ledger) == 2
    assert ledger[1]["rows_inserted"] == 0
    assert ledger[1]["rows_skipped_duplicate"] == 12
    assert ledger[0]["file_hash"] == ledger[1]["file_hash"]


def test_every_article_traces_to_an_import(conn, campaign_id, sample_csv):
    csv_generic.import_file(conn, campaign_ref=campaign_id, path=sample_csv)
    orphans = query(conn, """
        SELECT COUNT(*) AS n FROM articles a
         LEFT JOIN imports i ON i.id = a.import_id
         WHERE i.id IS NULL
    """)
    assert int(orphans[0]["n"]) == 0


def test_the_mapping_actually_applied_is_recorded(conn, campaign_id, sample_csv):
    import json

    report = csv_generic.import_file(conn, campaign_ref=campaign_id, path=sample_csv)
    row = query(conn, "SELECT mapping FROM imports WHERE id = ?", (report.import_id,))[0]
    mapping = json.loads(row["mapping"])
    assert mapping["headline"] == "Headline"
    assert mapping["published_at"] == "Date"
    assert mapping["outlet_name"] == "Source"


def test_column_mapping_can_be_guessed_and_overridden(sample_csv):
    info = csv_generic.sniff(sample_csv)
    assert info["required_present"] is True
    assert info["guessed_mapping"]["body_text"] == "Body"
    assert info["delimiter"] == ","


def test_import_refuses_a_file_with_no_date_column(conn, campaign_id, tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("Headline,Outlet\nSomething happened,Nordic Daily\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no column mapped to published_at"):
        csv_generic.import_file(conn, campaign_ref=campaign_id, path=path)


def test_rows_without_a_usable_date_are_rejected_not_guessed(conn, campaign_id, tmp_path):
    path = tmp_path / "partial.csv"
    path.write_text(
        "Headline,Date,Source\n"
        "Good row,2019-03-04,Nordic Daily\n"
        "Bad row,not a date,Nordic Daily\n"
        "No date row,,Nordic Daily\n",
        encoding="utf-8",
    )
    report = csv_generic.import_file(conn, campaign_ref=campaign_id, path=path)
    assert report.inserted == 1
    assert report.rejected == 2
    assert any("unparseable publication date" in r["reason"] for r in report.rejections)


def test_normalise_row_never_invents_an_outlet():
    row, reason = normalise_row({"headline": "X", "published_at": "2019-03-04"})
    assert row is None
    assert "no outlet name" in reason


def test_normalise_row_derives_an_outlet_from_the_url_domain():
    row, _ = normalise_row({
        "headline": "X", "published_at": "2019-03-04",
        "url": "https://www.nordicdaily.example/story/1",
    })
    assert row.outlet_domain == "nordicdaily.example"
    assert row.outlet_name == "nordicdaily.example"


# ------------------------------------------------------------------ archive-export mappers

FACTIVA_SAMPLE = """
HD  Fishmeal for European fish farms traced to West African waters
BY  By Karin Holm
WC  812 words
PD  4 March 2019
SN  Nordic Daily
SC  NORDD
LA  English
CY  Copyright 2019 Nordic Daily
LP
    A new investigation published today alleges that fishmeal destined for European
    aquaculture feed is produced from fish local communities rely on.
TD
    The report names several processing plants and traces shipments north.
    See https://nordicdaily.example/a for the full text.
Document NORDD00020190304ef34000

HD  Nordic feed buyers question fishmeal origin after report
BY  Sofie Bruun
PD  6 March 2019
SN  Trade Weekly
LA  Danish
LP
    Feed producers face renewed questions from buyers about fishmeal provenance.
Document TRADEW00020190306ab12000
"""

NEXIS_SAMPLE = """
1 of 2 DOCUMENTS

                      Copyright 2019 Nordic Daily
                            Nordic Daily

March 4, 2019 Monday

SECTION: Business; Pg. 14
LENGTH: 812 words
HEADLINE: Fishmeal for European fish farms traced to West African waters
BYLINE: Karin Holm
LANGUAGE: ENGLISH

BODY:
A new investigation published today alleges that fishmeal destined for European aquaculture
feed is produced from fish that local communities rely on for food.

LOAD-DATE: March 5, 2019

2 of 2 DOCUMENTS

                      Copyright 2019 Trade Weekly
                            Trade Weekly

March 6, 2019 Wednesday

LENGTH: 400 words
HEADLINE: Nordic feed buyers question fishmeal origin after report
BYLINE: Sofie Bruun

BODY:
Feed producers across the Nordic region face renewed questions about provenance.

LOAD-DATE: March 7, 2019
"""


def test_factiva_article_blocks_parse():
    rows = factiva.parse_text(FACTIVA_SAMPLE)
    assert len(rows) == 2
    first = rows[0]
    assert first["headline"] == "Fishmeal for European fish farms traced to West African waters"
    assert first["published_at"] == "4 March 2019"
    assert first["outlet_name"] == "Nordic Daily"
    assert first["byline"] == "By Karin Holm"
    assert first["language"] == "en"
    assert "processing plants" in first["body_text"]
    assert first["url"] == "https://nordicdaily.example/a"
    assert rows[1]["language"] == "da"


def test_factiva_import_end_to_end(conn, campaign_id, tmp_path):
    path = tmp_path / "factiva.txt"
    path.write_text(FACTIVA_SAMPLE, encoding="utf-8")
    report = factiva.import_file(conn, campaign_ref=campaign_id, path=path)
    assert report.inserted == 2
    rows = query(conn, """
        SELECT a.headline, a.day_index, o.name AS outlet, i.source
          FROM articles a JOIN outlets o ON o.id = a.outlet_id
          JOIN imports i ON i.id = a.import_id
         WHERE a.campaign_id = ? ORDER BY a.published_at
    """, (campaign_id,))
    assert [r["outlet"] for r in rows] == ["Nordic Daily", "Trade Weekly"]
    assert [r["day_index"] for r in rows] == [0, 2]
    assert {r["source"] for r in rows} == {"factiva"}


def test_nexis_article_blocks_parse():
    rows = nexis.parse_text(NEXIS_SAMPLE)
    assert len(rows) == 2
    first = rows[0]
    assert first["headline"] == "Fishmeal for European fish farms traced to West African waters"
    assert first["published_at"] == "March 4, 2019"
    assert first["outlet_name"] == "Nordic Daily"
    assert first["byline"] == "Karin Holm"
    assert first["language"] == "en"
    assert "local communities" in first["body_text"]
    # The LOAD-DATE trailer must not be swallowed into the body.
    assert "LOAD-DATE" not in first["body_text"]


def test_nexis_import_end_to_end(conn, campaign_id, tmp_path):
    path = tmp_path / "nexis.txt"
    path.write_text(NEXIS_SAMPLE, encoding="utf-8")
    report = nexis.import_file(conn, campaign_ref=campaign_id, path=path)
    assert report.inserted == 2
    rows = query(conn, """
        SELECT a.day_index, o.name AS outlet FROM articles a
          JOIN outlets o ON o.id = a.outlet_id WHERE a.campaign_id = ? ORDER BY a.published_at
    """, (campaign_id,))
    assert [r["outlet"] for r in rows] == ["Nordic Daily", "Trade Weekly"]
    assert [r["day_index"] for r in rows] == [0, 2]


def test_rtf_and_html_wrappers_are_unwrapped():
    from cib.ingest.richtext import html_to_text, rtf_to_text

    rtf = r"{\rtf1\ansi\deff0 {\fonttbl{\f0 Times;}}\f0\fs24 HEADLINE: Something\par BODY:\par Text here.\par}"
    assert "HEADLINE: Something" in rtf_to_text(rtf)
    assert "Times" not in rtf_to_text(rtf)

    html = "<html><head><style>p{color:red}</style></head><body><p>Hello</p><p>World &amp; more</p></body></html>"
    text = html_to_text(html)
    assert "Hello" in text and "World & more" in text and "color:red" not in text
