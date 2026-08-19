"""Documentation that must not drift from the code."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_metrics_doc_is_in_sync_with_the_definitions():
    """docs/METRICS.md is generated. If a definition changed, regenerate it with `make docs`."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "render_metrics_doc.py"), "--check"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr


def test_readme_documents_every_metric():
    """The README has to name every metric, because the definitions get argued about."""
    from cib.metrics import METRIC_ORDER
    from cib.metrics import definitions as metric_definitions

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    metrics_doc = (ROOT / "docs" / "METRICS.md").read_text(encoding="utf-8")
    combined = readme + metrics_doc
    for key in METRIC_ORDER:
        label = metric_definitions.get(key).label
        assert label in combined, f"{label} ({key}) is documented nowhere"


def test_env_example_lists_every_config_key_the_code_reads():
    """A key the code reads but .env.example does not mention is a key nobody will ever set."""
    import re

    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    documented = set(re.findall(r"^([A-Z][A-Z0-9_]+)=", example, re.MULTILINE))

    used: set[str] = set()
    for path in (ROOT / "backend" / "cib").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        used |= set(re.findall(r'config\.get(?:_bool)?\(\s*"([A-Z][A-Z0-9_]+)"', source))
        used |= set(re.findall(r'os\.environ\.get\(\s*"(CIB_[A-Z0-9_]+)"', source))

    undocumented = sorted(used - documented)
    assert not undocumented, f"Not in .env.example: {', '.join(undocumented)}"


def test_no_secret_looking_value_is_committed_in_env_example():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for line in example.splitlines():
        if "=" not in line or line.strip().startswith("#"):
            continue
        key, _, value = line.partition("=")
        if any(word in key for word in ("KEY", "TOKEN", "PASSWORD", "SECRET")):
            assert value.strip() == "", f"{key} has a value in .env.example"
