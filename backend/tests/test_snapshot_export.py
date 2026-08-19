"""The build-time snapshot that backs the GitHub Pages build.

The property under test is that the static site says exactly what the live tool says. If the
snapshot could drift from `cib.metrics`, the published dashboard would be a second, unverified
implementation of the metrics — which is the thing the architecture exists to prevent.
"""

from __future__ import annotations

import json

import pytest

from cib import dedup
from cib.export import snapshot_json
from cib.ingest import csv_generic
from cib.metrics import METRIC_ORDER, campaign_metrics
from cib.metrics import definitions as metric_definitions
from cib.repo import campaigns as campaign_repo
from cib.repo import events as event_repo


@pytest.fixture
def populated(conn, sample_csv):
    """Two published campaigns plus a pre-publication one — the shapes the snapshot must handle."""
    for name, slug in (("Campaign A", "a"), ("Campaign B", "b")):
        cid = campaign_repo.create(
            conn, name=name, slug=slug, publisher_org="Publisher", campaign_type="ngo_report",
            status="archived", published_at="2019-03-04T00:00:00", timezone="Europe/Copenhagen",
        )
        csv_generic.import_file(conn, campaign_ref=cid, path=sample_csv,
                                default_tier="national_general")
        dedup.cluster_campaign(conn, cid)
    forthcoming = campaign_repo.create(
        conn, name="Forthcoming", slug="soon", publisher_org="Publisher",
        campaign_type="journalism", status="pre_publication", published_at=None,
    )
    campaign_repo.add_precedent(conn, forthcoming, campaign_repo.get_by_slug(conn, "a").id,
                                rationale="Same publisher.")
    event_repo.add_escalation(
        conn, campaign_id=campaign_repo.get_by_slug(conn, "a").id,
        occurred_at="2019-03-20T00:00:00", escalation_type="regulatory_action",
        actor_name="Example Authority", description="Inquiry opened.",
        source_url="https://authority.example/inquiry", severity=4,
    )
    return conn


def test_snapshot_values_match_the_metrics_module(populated, conn):
    """Every baked figure equals what campaign_metrics returns for the same campaign and cutoff."""
    payload = snapshot_json.build_payload(conn)

    for slug, detail in payload["campaign_detail"].items():
        campaign = campaign_repo.get_by_slug(conn, slug)
        for cutoff_key, baked in detail["metrics_by_cutoff"].items():
            cutoff = None if cutoff_key == "lifetime" else int(cutoff_key)
            live = campaign_metrics(conn, campaign.id, cutoff).to_dict()
            for key, metric in live["metrics"].items():
                assert baked["metrics"][key]["value"] == metric["value"], f"{slug}/{key}"
                assert baked["metrics"][key]["row_ids"] == metric["row_ids"], f"{slug}/{key}"
                assert baked["metrics"][key]["available"] == metric["available"]


def test_every_row_id_resolves_in_the_baked_evidence_index(populated, conn):
    """Traceability has to survive the static build, or the drill-down silently shows nothing."""
    payload = snapshot_json.build_payload(conn)
    evidence = payload["evidence"]

    checked = 0
    for detail in payload["campaign_detail"].values():
        for baked in detail["metrics_by_cutoff"].values():
            for key, metric in baked["metrics"].items():
                if not metric["row_ids"]:
                    continue
                store = evidence[metric["row_table"]]
                for row_id in metric["row_ids"]:
                    assert str(row_id) in store, f"{key} points at missing {metric['row_table']}"
                    checked += 1
    assert checked > 0, "the fixture produced no row ids to check"


def test_stripped_metric_fields_are_recoverable_from_meta(populated, conn):
    """The frontend rejoins label/unit/definition by key. Every key must be present in meta."""
    payload = snapshot_json.build_payload(conn)
    definitions = payload["meta"]["definitions"]

    for detail in payload["campaign_detail"].values():
        for baked in detail["metrics_by_cutoff"].values():
            for key, metric in baked["metrics"].items():
                assert "label" not in metric, "label should be deduplicated into meta"
                assert "definition" not in metric
                assert key in definitions, f"{key} has no entry in meta.definitions"
                assert definitions[key]["plain"] and definitions[key]["counts"]


def test_every_comparison_column_reference_resolves(populated, conn):
    """Comparisons store (slug, cutoff) references rather than copies. They must all exist."""
    payload = snapshot_json.build_payload(conn)
    assert payload["comparisons"], "no comparisons were baked"

    for key, entry in payload["comparisons"].items():
        assert entry["columns"], f"{key} has no columns"
        for column in entry["columns"]:
            detail = payload["campaign_detail"][column["slug"]]
            assert column["cutoff"] in detail["metrics_by_cutoff"], (
                f"{key} references {column['slug']} at cutoff {column['cutoff']}, "
                "which was not baked"
            )
            assert column["cutoff"] in detail["series_by_cutoff"]


def test_a_pre_publication_campaign_bakes_no_footprint(populated, conn):
    payload = snapshot_json.build_payload(conn)
    detail = payload["campaign_detail"]["soon"]

    assert detail["metrics_by_cutoff"] == {}
    assert detail["pre_publication"] is not None
    assert detail["pre_publication"]["footprint_available"] is False
    assert detail["pre_publication"]["precedent_count"] == 1


def test_an_empty_database_warns_rather_than_showing_zeros(conn):
    campaign_repo.create(
        conn, name="Nothing imported", slug="empty", publisher_org="P",
        campaign_type="ngo_report", status="archived", published_at="2019-01-01T00:00:00",
    )
    payload = snapshot_json.build_payload(conn)
    assert any("empty dataset" in w for w in payload["warnings"])


def test_extra_warnings_are_carried_into_the_snapshot(conn):
    """This is how a demo-data build labels itself on every page."""
    payload = snapshot_json.build_payload(conn, extra_warnings=["SYNTHETIC DEMO DATA."])
    assert payload["warnings"][0] == "SYNTHETIC DEMO DATA."


def test_the_committed_snapshot_is_valid_and_covers_every_metric():
    """The snapshot in the repo is what GitHub Pages serves, so it is checked directly."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    seed = json.loads((root / "web" / "src" / "lib" / "seed.json").read_text(encoding="utf-8"))

    assert seed["mode"] == "snapshot"
    assert seed["meta"]["metric_order"] == METRIC_ORDER
    for key in METRIC_ORDER:
        assert key in seed["meta"]["definitions"], f"{key} missing from the committed snapshot"
        assert seed["meta"]["definitions"][key]["plain"] == metric_definitions.get(key).plain


def test_the_committed_snapshot_contains_no_real_coverage():
    """The repository is public. The committed database must stay free of imported coverage.

    Real campaign names, escalation logs and logged internal signals are commercially sensitive;
    this guard fails the build if a working database is ever committed by accident.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    seed = json.loads((root / "web" / "src" / "lib" / "seed.json").read_text(encoding="utf-8"))

    assert seed["stats"]["articles"] == 0, (
        "The committed snapshot contains imported articles. Publishing real coverage from a "
        "public repository leaks commercially sensitive monitoring. Run `make snapshot` against "
        "the seeded state database before committing."
    )
    assert any("empty dataset" in w or "SYNTHETIC" in w.upper() for w in seed["warnings"])
