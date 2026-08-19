"""Syndication clustering, checked against a hand-counted fixture.

The fixture (fixtures/syndication_sample.csv) contains exactly 12 articles, hand-counted:

  * Story A — one original plus three byte-identical republications across four outlets
    (Nordic Daily, Global Wire Service, Regional Post, Business Times). Body hashes are equal, so
    all four cluster. **4 members, method=hash.**
  * Story B — Trade Weekly's original and Feed Industry News' republication carry the same
    headline apart from one comma; token-set ratio 1.0, so they cluster.
    **2 members, method=title_similarity.**
  * Story B near-miss — Aqua Trade Journal ran "Fishmeal origin questioned by Nordic feed buyers
    after report" against Trade Weekly's "Nordic feed buyers question fishmeal origin after
    report". Token-set ratio is 0.78, below the 0.9 threshold, and the body was rewritten so
    shingle overlap is low too. It stays **unclustered, deliberately** — this is the case that
    proves the threshold discriminates rather than swallowing anything that looks similar.
  * Five further articles with no syndication at all.

Hand count: 12 articles · 2 clusters covering 6 articles · 6 unclustered ·
**8 unique stories** · **9 unique outlets** · syndication ratio 9/8 = 1.125.
"""

from __future__ import annotations

from cib import dedup
from cib.db import query
from cib.ingest import csv_generic


def test_fixture_imports_all_twelve_rows(conn, campaign_id, sample_csv):
    report = csv_generic.import_file(conn, campaign_ref=campaign_id, path=sample_csv)
    assert report.row_count == 12
    assert report.inserted == 12
    assert report.rejected == 0


def test_hand_counted_cluster_shape(loaded, conn):
    report = dedup.cluster_campaign(conn, loaded)
    assert report.clusters_created == 2
    assert report.articles_clustered == 6

    clusters = query(conn, "SELECT * FROM clusters WHERE campaign_id = ? ORDER BY id", (loaded,))
    assert sorted(int(c["member_count"]) for c in clusters) == [2, 4]
    methods = {c["method"] for c in clusters}
    assert methods == {"hash", "title_similarity"}


def test_duplicates_are_marked_never_deleted(loaded, conn):
    total = query(conn, "SELECT COUNT(*) AS n FROM articles WHERE campaign_id = ?", (loaded,))
    assert int(total[0]["n"]) == 12, "clustering must never remove a row"

    originals = query(
        conn, "SELECT COUNT(*) AS n FROM articles WHERE campaign_id = ? AND is_original = 1",
        (loaded,),
    )
    # One representative per cluster (2) plus every unclustered article (6).
    assert int(originals[0]["n"]) == 8


def test_every_membership_records_its_evidence(loaded, conn):
    rows = query(conn, """
        SELECT cm.method, cm.similarity, cm.matched_against_article_id
          FROM cluster_members cm
          JOIN clusters c ON c.id = cm.cluster_id
         WHERE c.campaign_id = ?
    """, (loaded,))
    assert len(rows) == 6
    for row in rows:
        assert row["method"] in {"hash", "title_similarity", "shingle", "manual"}
        assert row["similarity"] is not None


def test_clustering_is_idempotent(loaded, conn):
    first = dedup.cluster_campaign(conn, loaded)
    second = dedup.cluster_campaign(conn, loaded)
    assert (first.clusters_created, first.articles_clustered) == \
           (second.clusters_created, second.articles_clustered)
    assert int(query(conn, "SELECT COUNT(*) AS n FROM clusters WHERE campaign_id = ?",
                     (loaded,))[0]["n"]) == 2


def test_manual_clusters_survive_a_rerun(loaded, conn):
    ids = [int(r["id"]) for r in query(
        conn,
        "SELECT id FROM articles WHERE campaign_id = ? AND cluster_id IS NULL ORDER BY id LIMIT 2",
        (loaded,),
    )]
    manual_id = dedup.merge_manual(conn, loaded, ids)
    dedup.cluster_campaign(conn, loaded)
    survived = query(conn, "SELECT * FROM clusters WHERE id = ?", (manual_id,))
    assert survived and survived[0]["method"] == "manual"
    still_members = query(
        conn, "SELECT article_id FROM cluster_members WHERE cluster_id = ?", (manual_id,)
    )
    assert {int(r["article_id"]) for r in still_members} == set(ids)


def test_reworded_headline_below_the_threshold_stays_unclustered(loaded, conn):
    """The 0.9 token-set threshold has to discriminate, not swallow anything vaguely similar.

    Aqua Trade Journal's rewording of the Trade Weekly headline scores 0.78. Clustering it would
    understate unique stories and overstate the syndication ratio, so it is left alone.
    """
    row = query(conn, """
        SELECT a.cluster_id FROM articles a JOIN outlets o ON o.id = a.outlet_id
         WHERE a.campaign_id = ? AND o.name = 'Aqua Trade Journal'
    """, (loaded,))
    assert len(row) == 1
    assert row[0]["cluster_id"] is None


def test_threshold_scores_are_what_we_think_they_are(conn):
    """Pins the similarity scores the hand count above depends on."""
    from cib.textutil import token_set_ratio

    original = "Nordic feed buyers question fishmeal origin after report"
    comma_variant = "Nordic feed buyers question fishmeal origin, after report"
    reworded = "Fishmeal origin questioned by Nordic feed buyers after report"

    assert token_set_ratio(original, comma_variant) == 1.0
    assert token_set_ratio(original, reworded) < dedup.TITLE_THRESHOLD
