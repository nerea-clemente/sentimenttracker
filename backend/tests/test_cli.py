"""CLI smoke tests.

The metrics module was already well covered when a shadowed import broke `cib compare` and
`cib metrics` at runtime: the tests called the functions directly and never went through the
command line. These tests exercise every command the way a user does.
"""

from __future__ import annotations

import json

import pytest

from cib.cli import main


@pytest.fixture
def db(tmp_path, sample_csv):
    """A database with two comparable campaigns and one pre-publication campaign, via the CLI."""
    path = str(tmp_path / "cli.sqlite3")
    base = ["--db", path, "--as", "test:cli"]
    assert main([*base, "init"]) == 0
    assert main([*base, "campaign", "add", "--name", "Campaign One", "--publisher", "P1",
                 "--type", "ngo_report", "--status", "archived",
                 "--published-at", "2019-03-04T00:00:00", "--slug", "one"]) == 0
    assert main([*base, "campaign", "add", "--name", "Campaign Two", "--publisher", "P2",
                 "--type", "coalition", "--status", "archived",
                 "--published-at", "2019-03-04T00:00:00", "--slug", "two"]) == 0
    assert main([*base, "campaign", "add", "--name", "Forthcoming", "--publisher", "P1",
                 "--type", "journalism", "--status", "pre_publication", "--slug", "soon"]) == 0
    for slug in ("one", "two"):
        assert main([*base, "import", "csv", "--campaign", slug,
                     "--file", str(sample_csv), "--default-tier", "national_general"]) == 0
    return base


def test_init_and_seed_run(tmp_path, capsys):
    base = ["--db", str(tmp_path / "seed.sqlite3")]
    assert main([*base, "init"]) == 0
    assert main([*base, "seed"]) == 0
    assert main([*base, "campaign", "list"]) == 0
    output = capsys.readouterr().out
    assert "seed-forthcoming-fishmeal-supply-chain-investigation" in output
    assert "pre_publication" in output


def test_seed_is_idempotent(tmp_path, capsys):
    base = ["--db", str(tmp_path / "seed.sqlite3")]
    main([*base, "init"])
    main([*base, "seed"])
    assert main([*base, "seed"]) == 0
    assert "already present" in capsys.readouterr().out


def test_compare_runs_and_reports_a_cutoff(db, capsys):
    assert main([*db, "compare", "one", "two", "--at-day", "5"]) == 0
    output = capsys.readouterr().out
    assert "Comparison at day 5" in output
    assert "explicit cutoff" in output
    assert "Unique stories" in output
    assert "Unique outlets" in output


def test_compare_json_carries_row_ids(db, capsys):
    assert main([*db, "--json", "compare", "one", "two", "--at-day", "5"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["at_day_index"] == 5
    for column in payload["campaigns"]:
        assert column["metrics"]["unique_stories"]["row_ids"]


def test_metrics_command_runs(db, capsys):
    assert main([*db, "metrics", "one"]) == 0
    output = capsys.readouterr().out
    assert "Unique stories" in output
    assert "source rows" in output
    assert "Data quality" in output


def test_metrics_on_a_pre_publication_campaign_refuses_and_falls_back(db, capsys):
    assert main([*db, "metrics", "soon"]) == 0
    output = capsys.readouterr().out
    assert "no publication date" in output
    assert '"footprint_available": false' in output
    assert "publisher_precedent" in output


def test_compare_rejects_a_single_campaign(db, capsys):
    assert main([*db, "compare", "one"]) == 1
    assert "at least two" in capsys.readouterr().err


def test_definitions_command_prints_every_metric(db, capsys):
    assert main([*db, "definitions"]) == 0
    output = capsys.readouterr().out
    assert "Unique stories" in output
    assert "Counts:" in output
    assert "Severity-weighted escalation total" in output


def test_import_inspect_reports_the_guessed_mapping(db, sample_csv, capsys):
    assert main([*db, "import", "inspect", "--file", str(sample_csv)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["required_present"] is True
    assert payload["guessed_mapping"]["headline"] == "Headline"


def test_reimport_via_cli_is_a_noop(db, sample_csv, capsys):
    assert main([*db, "import", "csv", "--campaign", "one", "--file", str(sample_csv)]) == 0
    output = capsys.readouterr().out
    assert "0 new" in output
    assert "12 already present" in output
    assert "No duplicates created" in output


def test_escalation_add_requires_a_source_url(db):
    with pytest.raises(SystemExit):
        main([*db, "escalation", "add", "--campaign", "one", "--occurred-at", "2019-03-08",
              "--type", "regulatory_action", "--actor", "A", "--description", "D",
              "--severity", "4"])


def test_escalation_lifecycle(db, capsys):
    assert main([*db, "escalation", "add", "--campaign", "one", "--occurred-at", "2019-03-08",
                 "--type", "regulatory_action", "--actor", "Example Authority",
                 "--description", "Inquiry opened", "--severity", "4",
                 "--source-url", "https://authority.example/inquiry"]) == 0
    assert "Regulatory investigation opened" in capsys.readouterr().out

    assert main([*db, "escalation", "verify", "1"]) == 0
    assert main([*db, "escalation", "list", "one"]) == 0
    listed = capsys.readouterr().out
    assert "✓" in listed
    assert "https://authority.example/inquiry" in listed


def test_outlet_reach_requires_a_source(db):
    with pytest.raises(SystemExit):
        main([*db, "outlet", "reach", "1", "--value", "1000"])


def test_outlet_reach_records_provenance(db, capsys):
    assert main([*db, "outlet", "reach", "1", "--value", "210000",
                 "--source", "Danske Medier readership survey 2024"]) == 0
    assert "Danske Medier" in capsys.readouterr().out


def test_entity_match_reports_coverage(db, capsys):
    assert main([*db, "entity", "add", "--name", "Nordisk Aqua Feed", "--type", "own_company",
                 "--alias", "European aquaculture feed"]) == 0
    capsys.readouterr()
    assert main([*db, "entity", "match", "one"]) == 0
    payload = json.loads(capsys.readouterr().out.split("\n\n")[0])
    assert payload["articles_scanned"] == 12
    assert payload["mentions_created"] > 0


def test_cluster_command_runs(db, capsys):
    assert main([*db, "cluster", "one"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["clusters_created"] == 2
    assert payload["articles_clustered"] == 6


def test_snapshot_command_writes_rows(db, capsys):
    assert main([*db, "snapshot", "--all"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["one"] == 36   # days 0 to 35 inclusive


def test_watch_commands_run(db, capsys):
    assert main([*db, "watch", "add", "--name", "Publisher feed", "--type", "rss",
                 "--pattern", "https://publisher.example/feed", "--campaign", "soon",
                 "--disabled", "--promotes-to-live"]) == 0
    assert main([*db, "watch", "list"]) == 0
    output = capsys.readouterr().out
    assert "DISABLED" in output
    assert "promotes-to-live" in output

    assert main([*db, "watch", "enable", "1"]) == 0
    assert main([*db, "watch", "hits"]) == 0
    assert "No watch hits recorded" in capsys.readouterr().out


def test_export_briefing_to_a_file(db, tmp_path):
    out = tmp_path / "briefing.md"
    assert main([*db, "export", "briefing", "one", "two", "--at-day", "5",
                 "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "Campaign briefing" in text
    assert "Evidence appendix" in text
    assert "Comparison at day 5" in text


def test_export_comparison_csv(db, tmp_path):
    out = tmp_path / "comparison.csv"
    assert main([*db, "export", "comparison", "one", "two", "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "# Generated at (UTC):" in text
    assert "Unique stories" in text


def test_import_list_shows_the_ledger(db, capsys):
    assert main([*db, "import", "list"]) == 0
    output = capsys.readouterr().out
    assert "syndication_sample.csv" in output


def test_campaign_set_published_reindexes(db, capsys):
    assert main([*db, "campaign", "set-published", "soon", "2019-03-06T00:00:00"]) == 0
    output = capsys.readouterr().out
    assert "published_at = 2019-03-06T00:00:00" in output
    assert "Recomputed day_index" in output


def test_unknown_campaign_exits_nonzero(db, capsys):
    assert main([*db, "metrics", "does-not-exist"]) == 1
    assert "No campaign" in capsys.readouterr().err
