"""Data extraction, transformation, and export utilities."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from loguru import logger


class DataPipeline:
    @staticmethod
    def extract_tables(html: str) -> list[list[list[str]]]:
        """Extract tables from HTML string. Returns list of tables, each a list of rows."""
        try:
            from html.parser import HTMLParser

            class TableParser(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.tables: list[list[list[str]]] = []
                    self._row: list[str] = []
                    self._table: list[list[str]] = []
                    self._cell = ""
                    self._in_cell = False

                def handle_starttag(self, tag, attrs):
                    if tag == "table":
                        self._table = []
                    elif tag in ("td", "th"):
                        self._in_cell = True
                        self._cell = ""
                    elif tag == "tr":
                        self._row = []

                def handle_endtag(self, tag):
                    if tag in ("td", "th"):
                        self._in_cell = False
                        self._row.append(self._cell.strip())
                    elif tag == "tr":
                        if self._row:
                            self._table.append(self._row)
                    elif tag == "table":
                        if self._table:
                            self.tables.append(self._table)

                def handle_data(self, data):
                    if self._in_cell:
                        self._cell += data

            parser = TableParser()
            parser.feed(html)
            return parser.tables
        except Exception as e:
            logger.error(f"Table extraction failed: {e}")
            return []

    @staticmethod
    def to_csv(data: list[list[str]], headers: list[str] | None = None) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf)
        if headers:
            writer.writerow(headers)
        writer.writerows(data)
        return buf.getvalue()

    @staticmethod
    def to_json(data: Any, indent: int = 2) -> str:
        return json.dumps(data, indent=indent, ensure_ascii=False, default=str)

    @staticmethod
    def save_csv(data: list[list[str]], path: str, headers: list[str] | None = None):
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if headers:
                writer.writerow(headers)
            writer.writerows(data)
        logger.info(f"CSV saved to {path}")

    @staticmethod
    def save_json(data: Any, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        logger.info(f"JSON saved to {path}")

    @staticmethod
    def diff_data(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
        """Compute a simple diff between two data snapshots."""
        added = {k: v for k, v in new.items() if k not in old}
        removed = {k: v for k, v in old.items() if k not in new}
        changed = {k: {"old": old[k], "new": new[k]} for k in old if k in new and old[k] != new[k]}
        return {"added": added, "removed": removed, "changed": changed}
