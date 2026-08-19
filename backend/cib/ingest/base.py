"""Shared ingestion machinery.

An import is a transaction with a ledger entry: the file, its hash, how many rows came in, how
many were new, how many were already present and how many were rejected. Every article row points
back at that ledger entry, which is what makes a figure on screen traceable to a source file.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..repo import articles as article_repo
from ..repo import campaigns as campaign_repo
from ..repo import imports as import_repo
from ..repo import outlets as outlet_repo
from ..textutil import domain_of, sha256_file
from ..timeutil import parse_timestamp

# The canonical shape every mapper produces. Mappers translate their source format into these keys
# and nothing else; all validation and writing happens here.
CANONICAL_FIELDS = (
    "headline", "published_at", "url", "outlet_name", "outlet_domain",
    "country", "language", "byline", "body_text", "outlet_tier",
)


@dataclass
class ImportReport:
    import_id: int
    source: str
    file_name: str | None
    row_count: int = 0
    inserted: int = 0
    skipped_duplicate: int = 0
    rejected: int = 0
    rejections: list[dict[str, Any]] = field(default_factory=list)
    already_imported_as: int | None = None
    article_ids: list[int] = field(default_factory=list)

    def reject(self, row_number: int, reason: str, row: dict | None = None) -> None:
        self.rejected += 1
        # Keep a bounded sample: enough to diagnose a bad export, not enough to bloat the ledger.
        if len(self.rejections) < 50:
            self.rejections.append({"row": row_number, "reason": reason,
                                    "sample": {k: row.get(k) for k in list(row or {})[:6]}})

    def summary(self) -> str:
        parts = [
            f"import #{self.import_id} ({self.source})",
            f"{self.row_count} rows",
            f"{self.inserted} new",
            f"{self.skipped_duplicate} already present",
            f"{self.rejected} rejected",
        ]
        if self.already_imported_as is not None:
            note = f"identical file previously imported as #{self.already_imported_as}"
            if self.inserted:
                # Same file, different campaign: not a re-import, so say so rather than letting
                # the notice read as "nothing happened".
                note += " (into a different campaign — these rows are new here)"
            parts.append(note)
        return " | ".join(parts)


@dataclass
class NormalisedRow:
    headline: str
    published_at: str
    url: str | None = None
    outlet_name: str | None = None
    outlet_domain: str | None = None
    country: str | None = None
    language: str | None = None
    byline: str | None = None
    body_text: str | None = None
    outlet_tier: str | None = None
    raw: dict = field(default_factory=dict)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def normalise_row(row: dict, default_tier: str = "blog") -> tuple[NormalisedRow | None, str]:
    """Turn a mapped source row into a validated canonical row, or explain why it cannot be one.

    Nothing is filled in here. A missing outlet is derived from the URL's domain if one is present
    — which is reading the data, not inventing it — and otherwise the row is rejected.
    """
    headline = _clean(row.get("headline"))
    if not headline:
        return None, "no headline"

    published_raw = _clean(row.get("published_at"))
    if not published_raw:
        return None, "no publication date"
    parsed = parse_timestamp(published_raw)
    if parsed is None:
        return None, f"unparseable publication date: {published_raw!r}"

    url = _clean(row.get("url"))
    outlet_domain = domain_of(_clean(row.get("outlet_domain"))) or domain_of(url)
    outlet_name = _clean(row.get("outlet_name")) or outlet_domain
    if not outlet_name:
        return None, "no outlet name, outlet domain or URL to derive an outlet from"

    return NormalisedRow(
        headline=headline,
        published_at=parsed.isoformat(),
        url=url,
        outlet_name=outlet_name,
        outlet_domain=outlet_domain,
        country=(_clean(row.get("country")) or None),
        language=(_clean(row.get("language")) or None),
        byline=_clean(row.get("byline")),
        body_text=_clean(row.get("body_text")),
        outlet_tier=_clean(row.get("outlet_tier")) or default_tier,
        raw=row,
    ), ""


def run_import(
    conn: sqlite3.Connection,
    *,
    campaign_ref: str | int,
    source: str,
    rows: list[dict],
    file_path: str | Path | None = None,
    mapping: dict | None = None,
    default_tier: str = "blog",
    default_country: str | None = None,
    default_language: str | None = None,
    notes: str | None = None,
) -> ImportReport:
    """Write mapped rows into the database inside one import ledger entry.

    Re-running the same file is a no-op for the article table: existing rows are counted as
    already present, and a fresh ledger entry records that the re-import happened and found
    nothing new. That is deliberate — the ledger is an audit trail of what was attempted, not
    only of what changed.
    """
    campaign = campaign_repo.resolve(conn, campaign_ref)
    path = Path(file_path) if file_path else None
    file_hash = sha256_file(path) if path and path.exists() else None

    prior = import_repo.previous_with_hash(conn, file_hash) if file_hash else None
    import_id = import_repo.start(
        conn, source=source,
        file_name=path.name if path else None,
        file_hash=file_hash,
        mapping=mapping,
        notes=notes,
    )
    report = ImportReport(
        import_id=import_id, source=source, file_name=path.name if path else None,
        already_imported_as=int(prior["id"]) if prior else None,
    )

    for i, raw in enumerate(rows, start=1):
        report.row_count += 1
        normalised, reason = normalise_row(raw, default_tier=default_tier)
        if normalised is None:
            report.reject(i, reason, raw)
            continue

        outlet_id = outlet_repo.get_or_create(
            conn,
            name=normalised.outlet_name,
            domain=normalised.outlet_domain,
            country=normalised.country or default_country,
            language=normalised.language or default_language,
            tier=normalised.outlet_tier or default_tier,
        )
        result = article_repo.upsert(
            conn,
            campaign_id=campaign.id,
            outlet_id=outlet_id,
            headline=normalised.headline,
            published_at=normalised.published_at,
            import_id=import_id,
            campaign_published_at=campaign.published_at,
            campaign_timezone=campaign.timezone,
            url=normalised.url,
            language=normalised.language or default_language,
            country=normalised.country or default_country,
            byline=normalised.byline,
            body_text=normalised.body_text,
            source_row=normalised.raw,
        )
        if result.created:
            report.inserted += 1
        else:
            report.skipped_duplicate += 1
        if result.article_id:
            report.article_ids.append(result.article_id)

    import_repo.finish(
        conn, import_id,
        row_count=report.row_count, inserted=report.inserted,
        skipped=report.skipped_duplicate, rejected=report.rejected,
        notes=notes,
    )
    return report
