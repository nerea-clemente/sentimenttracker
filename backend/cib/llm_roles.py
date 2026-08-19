"""Optional LLM fallback for ambiguous entity-role assignment.

Scope, deliberately narrow:
  * It never finds mentions. The rules do that, from aliases.
  * It never produces a number, a reach figure, an audience estimate or a date.
  * It only chooses among the five defined roles for a mention that already exists and already
    has an evidence sentence attached.

Everything it decides is stored with classified_by='llm' and a confidence, so the whole set can be
excluded with one WHERE clause when a figure has to be defended.
"""

from __future__ import annotations

import json

from . import config
from .models import MENTION_ROLES

MODEL = "claude-sonnet-5"
MAX_BATCH = 25

_SYSTEM = (
    "You classify the ROLE that a named organisation plays in a single sentence from a news "
    "article. You are given the sentence and the organisation's name. Choose exactly one role:\n"
    "  subject            - the organisation is what the story is about, or is accused\n"
    "  named_supplier     - it is named as supplying or sourcing goods in the chain described\n"
    "  named_buyer        - it is named as buying or receiving goods in the chain described\n"
    "  quoted_response    - the sentence carries its reply, denial, statement or refusal to comment\n"
    "  passing_reference  - it is mentioned without any of the above\n"
    "Return JSON only. Do not infer beyond the sentence given. If the sentence does not support "
    "any stronger role, answer passing_reference. Never invent facts, figures or dates."
)


def available() -> bool:
    return bool(config.get("ANTHROPIC_API_KEY").strip())


def classify_batch(pending: list[dict]) -> list[dict]:
    """Classify ambiguous mentions. Returns [{mention_id, role, confidence}].

    Raises if the SDK or key is missing — the caller treats that as "leave the rule decision in
    place", never as an error that fails an import.
    """
    if not pending:
        return []
    key = config.get("ANTHROPIC_API_KEY").strip()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise RuntimeError(
            "The anthropic package is not installed. LLM role fallback is optional; "
            "rule-based roles are used without it."
        ) from exc

    client = Anthropic(api_key=key)
    results: list[dict] = []
    for i in range(0, len(pending), MAX_BATCH):
        batch = pending[i:i + MAX_BATCH]
        payload = [
            {"mention_id": m["mention_id"], "organisation": m["entity"], "sentence": m["sentence"]}
            for m in batch
        ]
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    "Classify each item. Respond with a JSON array of objects with keys "
                    '"mention_id", "role" and "confidence" (0-1). Nothing else.\n\n'
                    + json.dumps(payload, ensure_ascii=False, indent=1)
                ),
            }],
        )
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        results.extend(_parse(text))
    return results


def _parse(text: str) -> list[dict]:
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    out = []
    for item in parsed if isinstance(parsed, list) else []:
        role = item.get("role")
        if role not in MENTION_ROLES:
            continue
        try:
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError):
            continue
        out.append({
            "mention_id": int(item["mention_id"]),
            "role": role,
            "confidence": max(0.0, min(1.0, confidence)),
        })
    return out
