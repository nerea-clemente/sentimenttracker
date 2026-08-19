"""Infomedia export mapper (Danish coverage).

Infomedia is a licensed archive: its content cannot be scraped, so this reads the CSV or Excel
export a licensed user produces by hand. Column names differ between Infomedia's Danish and
English UI and between export profiles, so both spellings are recognised and the mapping actually
applied is recorded on the import ledger row.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .base import ImportReport, run_import
from .csv_generic import read_rows, sniff

# Canonical field -> Infomedia column spellings, Danish and English.
INFOMEDIA_HINTS: dict[str, tuple[str, ...]] = {
    "headline": ("overskrift", "rubrik", "headline", "titel", "title"),
    "published_at": ("dato", "publiceringsdato", "publiceret", "date", "publication date"),
    "url": ("url", "link", "webadresse", "artikel-url"),
    "outlet_name": ("medie", "kilde", "medienavn", "source", "media", "publication"),
    "outlet_domain": ("domæne", "domain", "website", "hjemmeside"),
    "byline": ("forfatter", "journalist", "byline", "author"),
    "body_text": ("brødtekst", "tekst", "indhold", "body", "text", "artikeltekst"),
    "language": ("sprog", "language"),
    "country": ("land", "country"),
}

# Infomedia's own section/media-type values, mapped to this tool's outlet tiers. Anything not
# listed here falls back to the --default-tier flag rather than being guessed.
TIER_HINTS: dict[str, str] = {
    "landsdækkende dagblad": "national_general",
    "landsdaekkende dagblad": "national_general",
    "national daily": "national_general",
    "erhvervsmedie": "national_business",
    "business": "national_business",
    "fagblad": "trade",
    "trade": "trade",
    "regionale og lokale aviser": "regional",
    "lokalaviser": "regional",
    "regional": "regional",
    "tv": "broadcast",
    "radio": "broadcast",
    "webkilder": "blog",
    "nyhedsbureau": "wire",
    "news agency": "wire",
}


def _map_headers(headers: list[str]) -> dict[str, str]:
    lowered = {h.strip().lower(): h for h in headers if h}
    mapping: dict[str, str] = {}
    for field, hints in INFOMEDIA_HINTS.items():
        for hint in hints:
            if hint in lowered:
                mapping[field] = lowered[hint]
                break
    return mapping


def _read_xlsx(path: Path) -> tuple[list[str], list[dict]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "Reading .xlsx exports needs openpyxl. Install the ingest extra: "
            "pip install -e '.[ingest]'  — or export the file as CSV instead."
        ) from exc
    wb = load_workbook(filename=str(path), read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows_iter = ws.iter_rows(values_only=True)
    header_row = next(rows_iter, None)
    if header_row is None:
        return [], []
    headers = [str(h).strip() if h is not None else "" for h in header_row]
    records = []
    for values in rows_iter:
        if values is None or all(v is None for v in values):
            continue
        records.append({
            headers[i]: (values[i] if i < len(values) else None)
            for i in range(len(headers)) if headers[i]
        })
    wb.close()
    return [h for h in headers if h], records


def inspect(path: str | Path, encoding: str = "utf-8-sig") -> dict:
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        headers, _ = _read_xlsx(p)
        mapping = _map_headers(headers)
        return {"path": str(p), "format": "xlsx", "headers": headers,
                "guessed_mapping": mapping,
                "required_present": all(f in mapping for f in ("headline", "published_at"))}
    info = sniff(p, encoding)
    info["guessed_mapping"] = {**info["guessed_mapping"], **_map_headers(info["headers"])}
    info["format"] = "csv"
    info["required_present"] = all(
        f in info["guessed_mapping"] for f in ("headline", "published_at")
    )
    return info


def import_file(
    conn: sqlite3.Connection,
    *,
    campaign_ref: str | int,
    path: str | Path,
    mapping: dict[str, str] | None = None,
    default_tier: str = "national_general",
    default_country: str = "DK",
    default_language: str = "da",
    encoding: str = "utf-8-sig",
    notes: str | None = None,
) -> ImportReport:
    """Import an Infomedia export.

    Country and language default to DK/da because that is what an Infomedia licence covers, but
    both are overridable and any value present on the row itself always wins.
    """
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        headers, records = _read_xlsx(p)
        resolved = mapping or _map_headers(headers)
        rows = [{field: rec.get(col) for field, col in resolved.items()} for rec in records]
    else:
        resolved = mapping or inspect(p, encoding)["guessed_mapping"]
        rows = read_rows(p, resolved, encoding)

    missing = [f for f in ("headline", "published_at") if f not in resolved]
    if missing:
        raise ValueError(
            f"Infomedia export {p.name} has no column mapped to {', '.join(missing)}. "
            "Run `cib import inspect --source infomedia` and pass --map field=column."
        )

    return run_import(
        conn,
        campaign_ref=campaign_ref,
        source="infomedia",
        rows=rows,
        file_path=p,
        mapping=resolved,
        default_tier=default_tier,
        default_country=default_country,
        default_language=default_language,
        notes=notes,
    )
