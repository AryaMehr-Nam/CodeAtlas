"""Legacy v1 CSV/JSON accounting exporter retained without active callers."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class LegacyInvoiceRow:
    """Flat representation used by deprecated nightly SFTP accounting drops."""

    invoice_code: str
    customer_ref: str
    net_cents: int
    tax_cents: int
    settled: bool


def export_legacy_billing_csv(rows: list[LegacyInvoiceRow]) -> str:
    """Serialize v1 accounting rows into an RFC-4180 CSV payload."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["invoice_code", "customer_ref", "net_cents", "tax_cents", "settled"])
    for row in rows:
        writer.writerow(
            [
                row.invoice_code,
                row.customer_ref,
                row.net_cents,
                row.tax_cents,
                "1" if row.settled else "0",
            ]
        )
    return buffer.getvalue()


def build_legacy_audit_manifest(batch_name: str, rows: list[LegacyInvoiceRow]) -> str:
    """Produce a JSON summary manifest for deprecated v1 reconciliation jobs."""
    total_net = sum(row.net_cents for row in rows)
    total_tax = sum(row.tax_cents for row in rows)
    manifest = {
        "batch_name": batch_name,
        "generated_at": datetime.now(UTC).isoformat(),
        "record_count": len(rows),
        "gross_cents": total_net + total_tax,
    }
    return json.dumps(manifest, indent=2, sort_keys=True)


def prune_stale_export_files(export_dir: Path, keep_latest: int = 5) -> list[str]:
    """Delete older v1 export artifacts beyond the configured retention count."""
    if not export_dir.exists():
        return []
    candidates = sorted(
        (path for path in export_dir.iterdir() if path.is_file() and path.suffix == ".csv"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    removed: list[str] = []
    for stale_file in candidates[keep_latest:]:
        stale_file.unlink(missing_ok=True)
        removed.append(stale_file.name)
    return removed
