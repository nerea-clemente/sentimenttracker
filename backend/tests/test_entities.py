"""Entity mention detection and role assignment."""

from __future__ import annotations

from cib import entities as matcher
from cib.db import query
from cib.metrics import campaign_metrics
from cib.models import Entity
from cib.repo import entities as entity_repo


def _entity(name: str, aliases: list[str] | None = None, type: str = "own_company") -> Entity:
    return Entity(id=1, name=name, type=type, aliases=__import__("json").dumps(aliases or []))


def test_aliases_match_case_and_accent_insensitively():
    entity = _entity("Nordisk Aqua Feed", ["NordiskAquaFeed A/S", "Nordisk Fôr"])
    body = "Reporters contacted nordisk aqua feed for comment about the shipments."
    assert len(matcher.find_occurrences(body, entity)) == 1

    body_accent = "The company Nordisk For has responded to the allegations in full."
    assert len(matcher.find_occurrences(body_accent, entity)) == 1


def test_overlapping_aliases_count_once():
    """The longest surface form wins, so a nested alias does not double-count a single mention."""
    entity = _entity("Nordisk Aqua Feed A/S", ["Nordisk Aqua Feed"])
    body = "Nordisk Aqua Feed A/S declined to comment on the findings of the investigation."
    assert len(matcher.find_occurrences(body, entity)) == 1


def test_short_aliases_are_refused():
    """A two-letter alias matches inside unrelated words; a poisoned count is worse than none."""
    entity = _entity("Nordisk Aqua Feed", ["NA"])
    body = "The national authority said NAFTA rules would not apply to the shipments."
    assert matcher.find_occurrences(body, entity) == []


def test_word_boundaries_are_respected():
    entity = _entity("Aqua")
    assert matcher.find_occurrences("The Aqua group responded.", entity)
    assert matcher.find_occurrences("Aquaculture feed producers responded.", entity) == []


def test_roles_come_from_the_sentence():
    assert matcher.classify_role("The company declined to comment on the report.")[0] \
        == "quoted_response"
    assert matcher.classify_role("The plant supplies fishmeal to European feed producers.")[0] \
        == "named_supplier"
    assert matcher.classify_role("The firm is a buyer of fishmeal from the region.")[0] \
        == "named_buyer"
    assert matcher.classify_role("The investigation into the company began last year.")[0] \
        == "subject"

    role, confidence = matcher.classify_role("The company was founded in 1974.")
    assert role == "passing_reference"
    assert confidence < 0.5, "an uncued sentence must be low confidence so it can be reviewed"


def test_matching_records_evidence_for_every_mention(conn, loaded):
    entity_repo.create(conn, name="Nordic Daily", type="competitor")
    entity_repo.create(conn, name="Fishmeal Holdings", type="own_company",
                       aliases=["fishmeal destined for European aquaculture feed"])
    report = matcher.match_campaign(conn, loaded)
    assert report.mentions_created > 0

    rows = query(conn, """
        SELECT m.evidence_sentence, m.confidence, m.classified_by FROM mentions m
          JOIN articles a ON a.id = m.article_id WHERE a.campaign_id = ?
    """, (loaded,))
    for row in rows:
        assert row["evidence_sentence"].strip(), "every mention must quote its evidence"
        assert row["classified_by"] == "rule"
        assert row["confidence"] is not None


def test_matching_is_idempotent(conn, loaded):
    entity_repo.create(conn, name="Fishmeal Holdings", type="own_company",
                       aliases=["European aquaculture feed"])
    first = matcher.match_campaign(conn, loaded)
    second = matcher.match_campaign(conn, loaded)
    assert first.mentions_created == second.mentions_created


def test_articles_without_body_text_are_reported_not_assumed_empty(conn, campaign_id):
    """A missing body means the mention count is unknown, never zero."""
    from cib.ingest.base import run_import

    entity_repo.create(conn, name="Fishmeal Holdings", type="own_company")
    run_import(conn, campaign_ref=campaign_id, source="gdelt", rows=[{
        "headline": "Something about the sector", "published_at": "2019-03-04",
        "url": "https://example.invalid/x", "outlet_name": "Example", "body_text": None,
    }])

    report = matcher.match_campaign(conn, campaign_id)
    assert report.articles_without_body == 1

    exposure = campaign_metrics(conn, campaign_id).get("own_company_mentions")
    assert exposure.basis["articles_with_body_text"] == 0
    assert exposure.basis["articles_total"] == 1
    assert any("floor" in c for c in exposure.caveats)


def test_depth_score_is_never_returned_without_its_components(conn, loaded):
    entity_repo.create(conn, name="Fishmeal Holdings", type="own_company",
                       aliases=["European aquaculture feed"])
    matcher.match_campaign(conn, loaded)

    depth = campaign_metrics(conn, loaded).get("depth_score")
    assert "components" in depth.basis
    assert set(depth.basis["components"]) == {
        "subject", "named_supplier", "named_buyer", "quoted_response", "passing_reference",
    }
    recomputed = sum(c["count"] * c["weight"] for c in depth.basis["components"].values())
    assert depth.value == recomputed
    assert any("only meaningful alongside its components" in c for c in depth.caveats)


def test_exposure_metrics_refuse_without_an_own_company_entity(conn, loaded):
    metric = campaign_metrics(conn, loaded).get("own_company_mentions")
    assert metric.available is False
    assert "own_company" in metric.unavailable_reason


def test_competitor_comparison_names_the_standing(conn, loaded):
    entity_repo.create(conn, name="Fishmeal Holdings", type="own_company",
                       aliases=["European aquaculture feed"])
    entity_repo.create(conn, name="Rival Feeds", type="competitor",
                       aliases=["Nordic feed buyers"])
    matcher.match_campaign(conn, loaded)

    comparison = campaign_metrics(conn, loaded).get("competitor_comparison")
    assert comparison.value in ("named most", "named least", "named alongside peers")
    assert "Fishmeal Holdings" in comparison.basis["per_entity"]
    assert "Rival Feeds" in comparison.basis["per_entity"]


def test_no_metric_reads_the_tone_table(conn, loaded):
    """Sentiment must never drive the comparison view, so no metric may consume it."""
    import re
    from pathlib import Path

    metrics_dir = Path(matcher.__file__).parent / "metrics"
    for path in metrics_dir.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"\barticle_tone\b", source), (
            f"{path.name} references article_tone; tone is a secondary label and must not feed "
            "a metric"
        )


def test_two_own_company_entities_refuse_rather_than_guess(conn, loaded):
    """Silently measuring exposure for the wrong company is the worst failure available here.

    `cib seed` creates a placeholder own_company entity. Adding a real one used to leave two, and
    the metrics picked the lower id — reporting a confident zero for a company nobody asked about.
    """
    entity_repo.create(conn, name="Placeholder Co", type="own_company")
    entity_repo.create(conn, name="Real Co", type="own_company", aliases=["European aquaculture feed"])
    matcher.match_campaign(conn, loaded)

    result = campaign_metrics(conn, loaded)
    for key in ("own_company_mentions", "depth_score", "first_mention_day"):
        metric = result.get(key)
        assert metric.available is False
        assert "2 entities are typed 'own_company'" in metric.unavailable_reason
        assert "Placeholder Co" in metric.unavailable_reason
        assert "Real Co" in metric.unavailable_reason
        assert "aliases" in metric.unavailable_reason


def test_one_own_company_entity_measures_normally(conn, loaded):
    entity_repo.create(conn, name="Real Co", type="own_company",
                       aliases=["European aquaculture feed"])
    matcher.match_campaign(conn, loaded)

    metric = campaign_metrics(conn, loaded).get("own_company_mentions")
    assert metric.available is True
    assert metric.value > 0
    assert metric.basis["entity"] == "Real Co"
