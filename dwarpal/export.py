"""Write the agent-facing documents to disk: a snapshot of exactly what the API serves for this merchant.

    python -m dwarpal export --out data

Three files, pretty-printed with sorted keys so regenerating is a no-op when nothing changed:
``.well-known/agent-commerce.json`` (discovery), ``feed.json`` (the catalog as an agent sees it) and
``policy.json`` (the merchant policy the gate enforces).
"""
from __future__ import annotations

import json
from pathlib import Path

from dwarpal.api import discovery_document, feed_document


def _write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8",
                    newline="\n")


def export_snapshot(ctx, out_dir: str) -> list[Path]:
    out = Path(out_dir)
    files = [
        (out / ".well-known" / "agent-commerce.json", discovery_document(ctx)),
        (out / "feed.json", feed_document(ctx)),
        (out / "policy.json", ctx.policies.get()),
    ]
    for path, doc in files:
        _write(path, doc)
    return [path for path, _ in files]
