"""Entity mention detection and role assignment.

Alias-based rule matching first, always. The rules are readable, deterministic, and cheap to
re-run, and every mention they produce carries the sentence that justifies it.

An LLM is only used as a fallback for role assignment on mentions the rules find but cannot
confidently classify — never to find mentions, and never to produce a number. It is off unless
CIB_LLM_ROLES_ENABLED=1, and anything it decides is stored with classified_by='llm' and a
confidence value so it can be filtered out or reviewed.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field

from . import config
from .db import query
from .models import Entity
from .repo import entities as entity_repo
from .textutil import first_sentence_containing

# Role cues, checked against the sentence a mention appears in. Ordered by weight: the strongest
# claim about the company's involvement wins.
ROLE_CUES: list[tuple[str, tuple[str, ...]]] = [
    ("quoted_response", (
        "told the", "said in a statement", "in a statement", "a spokesperson",
        "spokesman", "spokeswoman", "declined to comment", "did not respond",
        "responded", "denied", "rejected the", "in an emailed", "when contacted",
        "udtaler", "oplyser", "afviser", "i en udtalelse", "ønsker ikke at kommentere",
    )),
    ("named_supplier", (
        "supplies", "supplied", "supplier to", "sources from", "sourced from",
        "buys fishmeal from", "purchases from", "imports from", "sourcing from",
        "which supplies", "a supplier", "leverandør", "leverer til", "aftager fra",
    )),
    ("named_buyer", (
        "buyer of", "bought from", "customer of", "purchased by", "sells to",
        "which buys", "clients include", "køber", "kunde hos", "sælger til",
    )),
    ("subject", (
        "investigation into", "accused of", "at the centre of", "at the center of",
        "under investigation", "faces allegations", "named in the report",
        "the report names", "is alleged to", "under fire", "beskyldes for",
        "i centrum af", "undersøgelsen af",
    )),
]

_WORD_BOUNDARY_SAFE = re.compile(r"^\w.*\w$|^\w$")


@dataclass
class MatchReport:
    campaign_id: int
    articles_scanned: int = 0
    articles_without_body: int = 0
    mentions_created: int = 0
    by_role: dict[str, int] = field(default_factory=dict)
    by_entity: dict[str, int] = field(default_factory=dict)
    ambiguous: list[dict] = field(default_factory=list)

    def note(self, entity_name: str, role: str) -> None:
        self.by_role[role] = self.by_role.get(role, 0) + 1
        self.by_entity[entity_name] = self.by_entity.get(entity_name, 0) + 1


def _fold(text: str) -> str:
    """Casefold and strip accents, so 'Aquaförening' matches 'aquaforening'."""
    s = unicodedata.normalize("NFKD", text)
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


def _alias_pattern(surface: str) -> re.Pattern | None:
    """A word-boundary regex for one alias. Aliases under three characters are refused.

    A two-letter alias matches inside unrelated words often enough to poison the counts, and a
    poisoned count is worse than a missing one.
    """
    cleaned = surface.strip()
    if len(cleaned) < 3:
        return None
    escaped = re.escape(_fold(cleaned))
    # Allow any run of whitespace where the alias has a space.
    escaped = escaped.replace(r"\ ", r"\s+")
    left = r"\b" if _WORD_BOUNDARY_SAFE.match(cleaned) else ""
    return re.compile(f"{left}{escaped}(?![\\w-])")


def find_occurrences(body: str, entity: Entity) -> list[tuple[int, str]]:
    """Character offsets where any of an entity's surface forms occur. Longest form wins.

    Overlapping matches are collapsed so 'Nordic Aqua Feed A/S' is one mention rather than also
    counting the shorter alias 'Nordic Aqua Feed' nested inside it.
    """
    if not body:
        return []
    folded = _fold(body)
    hits: list[tuple[int, int, str]] = []
    for surface in sorted(entity.surface_forms, key=len, reverse=True):
        pattern = _alias_pattern(surface)
        if pattern is None:
            continue
        for match in pattern.finditer(folded):
            hits.append((match.start(), match.end(), surface))
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))

    kept: list[tuple[int, str]] = []
    last_end = -1
    for start, end, surface in hits:
        if start < last_end:
            continue
        kept.append((start, surface))
        last_end = end
    return kept


def classify_role(sentence: str) -> tuple[str, float]:
    """Assign a role from the sentence a mention appears in.

    Returns the role and a confidence. A sentence with no cue is a passing reference at low
    confidence, which is what flags it for the optional LLM pass or for human review.
    """
    lowered = _fold(sentence)
    for role, cues in ROLE_CUES:
        for cue in cues:
            if _fold(cue) in lowered:
                return role, 0.8
    return "passing_reference", 0.3


def match_campaign(
    conn: sqlite3.Connection,
    campaign_id: int,
    *,
    entity_types: tuple[str, ...] | None = None,
    reset: bool = True,
    use_llm: bool | None = None,
    llm_confidence_floor: float = 0.5,
) -> MatchReport:
    """Detect entity mentions across a campaign's articles and assign roles.

    Only articles with imported body text can be scanned. Articles without one are counted and
    reported, never treated as containing no mentions.
    """
    report = MatchReport(campaign_id=campaign_id)
    if reset:
        entity_repo.clear_for_campaign(conn, campaign_id)

    all_entities = entity_repo.list_all(conn)
    if entity_types:
        all_entities = [e for e in all_entities if e.type in entity_types]
    if not all_entities:
        return report

    rows = query(conn, """
        SELECT id, body_text, headline FROM articles WHERE campaign_id = ? ORDER BY id
    """, (campaign_id,))

    pending_llm: list[dict] = []

    for row in rows:
        report.articles_scanned += 1
        body = row["body_text"]
        # The headline is prepended so a company named only in the headline is still found; the
        # offset shift is accounted for when the evidence sentence is extracted.
        searchable = f"{row['headline']}\n\n{body}" if body else row["headline"]
        if not body:
            report.articles_without_body += 1

        for entity in all_entities:
            for offset, _surface in find_occurrences(searchable, entity):
                sentence = first_sentence_containing(searchable, offset)
                role, confidence = classify_role(sentence)
                mention_id = entity_repo.add_mention(
                    conn,
                    article_id=int(row["id"]),
                    entity_id=entity.id,
                    role=role,
                    evidence_sentence=sentence,
                    char_offset=offset,
                    confidence=confidence,
                    classified_by="rule",
                )
                if mention_id is None:
                    continue
                report.mentions_created += 1
                report.note(entity.name, role)
                if confidence < llm_confidence_floor:
                    record = {
                        "mention_id": mention_id, "article_id": int(row["id"]),
                        "entity": entity.name, "entity_type": entity.type,
                        "role": role, "confidence": confidence, "sentence": sentence,
                    }
                    report.ambiguous.append(record)
                    pending_llm.append(record)

    enabled = config.get_bool("CIB_LLM_ROLES_ENABLED", False) if use_llm is None else use_llm
    if enabled and pending_llm:
        _apply_llm_roles(conn, pending_llm, report)

    return report


def _apply_llm_roles(conn: sqlite3.Connection, pending: list[dict], report: MatchReport) -> None:
    """Re-classify ambiguous roles with an LLM.

    Deliberately narrow: the LLM never decides *whether* an entity is mentioned, only which of the
    five roles best describes a mention the rules already found and already have a sentence for.
    Every result is stored as classified_by='llm' with a confidence, so a reviewer can filter the
    whole set out with one WHERE clause.
    """
    try:
        from .llm_roles import classify_batch
    except ImportError:
        return
    try:
        decisions = classify_batch(pending)
    except Exception as exc:
        report.ambiguous.append({"llm_error": str(exc)})
        return
    for decision in decisions:
        mention_id = decision.get("mention_id")
        role = decision.get("role")
        confidence = decision.get("confidence")
        if not mention_id or (role not in dict(ROLE_CUES) and role != "passing_reference"):
            continue
        if confidence is None:
            continue
        conn.execute(
            "UPDATE mentions SET role = ?, confidence = ?, classified_by = 'llm' WHERE id = ?",
            (role, float(confidence), int(mention_id)),
        )
