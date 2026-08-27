"""
Incremental persistence.

One JSON object per line, appended immediately after each product is parsed.
A crash at product 900 therefore loses one row, not the run, and restarting
skips everything already collected.

Keyword merging: the same ASIN can surface under several keywords. The first
write wins for product attributes; subsequent sightings only append to
search_keywords, recorded in a sidecar file and merged at export time.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Optional

from config import settings
from src.scraper.logger import get_logger

log = get_logger()

_ALIAS_SUFFIX = ".keywords.jsonl"


class ProductStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or settings.RAW_JSONL
        self.alias_path = self.path.with_suffix(self.path.suffix + _ALIAS_SUFFIX)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: set[str] = self._load_seen()

    # -- state -------------------------------------------------------------
    def _load_seen(self) -> set[str]:
        if not self.path.exists():
            return set()
        seen = set()
        with open(self.path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    seen.add(json.loads(line)["asin"])
                except (ValueError, KeyError):
                    continue
        if seen:
            log.info("Resuming: %s products already collected", len(seen))
        return seen

    def has(self, asin: str) -> bool:
        return asin in self._seen

    def __len__(self) -> int:
        return len(self._seen)

    # -- writes ------------------------------------------------------------
    def add(self, row: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._seen.add(row["asin"])

    def add_keyword_alias(self, asin: str, keyword: str) -> None:
        """Record that an already-collected ASIN also appeared under `keyword`."""
        with open(self.alias_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"asin": asin, "keyword": keyword}) + "\n")

    # -- reads -------------------------------------------------------------
    def rows(self) -> Iterator[dict]:
        if not self.path.exists():
            return iter(())
        with open(self.path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue

    def aliases(self) -> dict[str, set[str]]:
        mapping: dict[str, set[str]] = {}
        if not self.alias_path.exists():
            return mapping
        with open(self.alias_path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                mapping.setdefault(record["asin"], set()).add(record["keyword"])
        return mapping
