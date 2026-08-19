"""API layer.

The property that matters: the API must not compute anything. Every figure it returns has to come
from cib.metrics, so there is exactly one definition of each metric in the system.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cib.api import main as api


@pytest.fixture
def client(conn, loaded):
    # Override the dependency rather than patching the module attribute: the routes hold a
    # reference to the original function object, which is also the override key.
    api.app.dependency_overrides[api.get_conn] = lambda: conn
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()


def test_metrics_endpoint_matches_the_metrics_module_exactly(client, conn, loaded):
    """No metric logic may be duplicated outside cib.metrics."""
    from cib.metrics import campaign_metrics

    slug = api.campaign_repo.get(conn, loaded).slug
    response = client.get(f"/api/campaigns/{slug}/metrics")
    assert response.status_code == 200

    from_module = campaign_metrics(conn, loaded, None).to_dict()
    from_api = response.json()
    for key, metric in from_module["metrics"].items():
        assert from_api["metrics"][key]["value"] == metric["value"]
        assert from_api["metrics"][key]["row_ids"] == metric["row_ids"]


def test_a_pre_publication_campaign_returns_409_not_zeros(client, conn):
    campaign_id = api.campaign_repo.create(
        conn, name="Forthcoming", publisher_org="P", campaign_type="journalism",
        status="pre_publication", published_at=None,
    )
    slug = api.campaign_repo.get(conn, campaign_id).slug

    response = client.get(f"/api/campaigns/{slug}/metrics")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "pre_publication"
    assert detail["use_instead"].endswith("/pre-publication")

    fallback = client.get(f"/api/campaigns/{slug}/pre-publication")
    assert fallback.status_code == 200
    assert fallback.json()["footprint_available"] is False


def test_evidence_resolves_a_metrics_row_ids(client, conn, loaded):
    slug = api.campaign_repo.get(conn, loaded).slug
    metrics = client.get(f"/api/campaigns/{slug}/metrics").json()
    row_ids = metrics["metrics"]["unique_stories"]["row_ids"]

    response = client.get("/api/evidence",
                          params={"table": "articles", "ids": ",".join(map(str, row_ids))})
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == len(row_ids) == 8
    # Provenance travels with every evidence row.
    for row in payload["rows"]:
        assert row["import_source"] and row["imported_at"] and row["outlet"]


def test_evidence_rejects_an_unknown_table(client):
    assert client.get("/api/evidence", params={"table": "secrets", "ids": "1"}).status_code == 422


def test_compare_endpoint_needs_two_campaigns(client, conn, loaded):
    slug = api.campaign_repo.get(conn, loaded).slug
    response = client.get("/api/compare", params=[("campaign", slug)])
    assert response.status_code == 400
    assert "at least two" in response.json()["detail"]


def test_compare_endpoint_applies_one_cutoff(client, conn, loaded, sample_csv):
    from cib import dedup
    from cib.ingest import csv_generic

    other = api.campaign_repo.create(
        conn, name="Second", publisher_org="P", campaign_type="coalition",
        status="archived", published_at="2019-03-04T00:00:00",
    )
    csv_generic.import_file(conn, campaign_ref=other, path=sample_csv)
    dedup.cluster_campaign(conn, other)

    slugs = [api.campaign_repo.get(conn, loaded).slug, api.campaign_repo.get(conn, other).slug]
    response = client.get("/api/compare",
                          params=[("campaign", s) for s in slugs] + [("at_day", 3)])
    payload = response.json()
    assert payload["at_day_index"] == 3
    assert {c["metrics"]["total_articles"]["value"] for c in payload["campaigns"]} == {7}


def test_reach_endpoint_refuses_an_unsourced_figure(client, conn, loaded):
    outlet_id = conn.execute("SELECT id FROM outlets LIMIT 1").fetchone()[0]
    response = client.post(f"/api/outlets/{outlet_id}/reach", json={"reach_value": 100})
    assert response.status_code == 400
    assert "reach_source" in response.json()["detail"]


def test_escalation_endpoint_requires_a_source_url(client, conn, loaded):
    slug = api.campaign_repo.get(conn, loaded).slug
    response = client.post(f"/api/campaigns/{slug}/escalations", json={
        "occurred_at": "2019-03-10T00:00:00", "escalation_type": "regulatory_action",
        "actor_name": "Authority", "description": "Inquiry opened", "severity": 4,
    })
    assert response.status_code == 400
    assert "source_url" in response.json()["detail"]


def test_meta_serves_the_definitions_so_the_frontend_hardcodes_nothing(client):
    payload = client.get("/api/meta").json()
    assert payload["metric_order"]
    assert set(payload["metric_order"]) <= set(payload["definitions"])
    assert payload["severity_anchors"]["5"].startswith("Binding regulatory")
    for definition in payload["definitions"].values():
        assert definition["plain"] and definition["counts"]


def test_watch_status_says_when_nothing_is_being_watched(client):
    payload = client.get("/api/watch/status").json()
    assert payload["last_run"] is None
    assert "Nothing is being watched" in payload["note"]


def test_csv_export_endpoint_returns_a_file(client, conn, loaded):
    slug = api.campaign_repo.get(conn, loaded).slug
    response = client.get(f"/api/export/{slug}/articles.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "syndication_sample.csv" in response.text
