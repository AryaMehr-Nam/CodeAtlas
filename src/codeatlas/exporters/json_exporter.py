"""Deterministic JSON exporter for CodeAtlas analysis results."""

from __future__ import annotations

import json

from codeatlas.models.analysis import AnalysisResult


def export_json(result: AnalysisResult) -> str:
    """Serialize an ``AnalysisResult`` into deterministic sorted, indented JSON."""
    payload = result.model_dump(mode="json")
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"
