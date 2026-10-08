"""Offline, deterministic football-only projection of an existing market cache.

No provider client, credentials, or production state are accessed. Definitions
are retained whole, including unsupported football families, to preserve the
inventory's known-ID boundary. Research loader limits remain unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from modelfc.oddspapi_tournament_research import (
    MAX_METADATA_BYTES, MAX_METADATA_ROWS, TournamentResearchError,
    _classify_metadata, _metadata_index, _read_regular, _write_bytes,
)

FILTER_VERSION = "football-whole-definitions-v1"
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_SOURCE_ROWS = 100_000


def _reject():
    raise TournamentResearchError("MARKET_METADATA_INVALID")


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        _reject()
    return result


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _reject()
        result[key] = value
    return result


def filter_metadata(raw: bytes) -> tuple[bytes, dict]:
    """Return canonical bytes and provenance, or reject without a partial result."""
    if not raw or len(raw) > MAX_SOURCE_BYTES:
        _reject()
    try:
        source = json.loads(raw, object_pairs_hook=_object,
                            parse_constant=lambda value: _reject(), parse_float=_float)
        if not isinstance(source, list) or not 1 <= len(source) <= MAX_SOURCE_ROWS:
            _reject()
        seen = set()
        retained = []
        counts = {}
        for row in source:
            # Use the research validator even for removed rows; malformed or
            # colliding source identities must never disappear through filtering.
            _metadata_index([row])
            identity = row["marketId"]
            if identity <= 0 or identity in seen or type(row.get("sportId")) is not int:
                _reject()
            seen.add(identity)
            for field in ("marketType", "period", "marketName"):
                if field in row and not isinstance(row[field], str):
                    _reject()
            if "playerProp" in row and type(row["playerProp"]) is not bool:
                _reject()
            for outcome in row["outcomes"]:
                if outcome["outcomeId"] <= 0:
                    _reject()
                if "outcomeName" in outcome and not isinstance(outcome["outcomeName"], str):
                    _reject()
            if row["sportId"] == 10:
                family = _classify_metadata(row)
                if family is not None and not row["outcomes"]:
                    _reject()
                retained.append(row)
                label = family or "UNSUPPORTED_FOOTBALL"
                counts[label] = counts.get(label, 0) + 1
        retained.sort(key=lambda row: row["marketId"])
        _metadata_index(retained)
        output = (json.dumps(retained, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
        _reject()
    if len(output) > MAX_METADATA_BYTES or len(retained) > MAX_METADATA_ROWS:
        raise TournamentResearchError("FILTERED_METADATA_LIMIT_EXCEEDED")
    return output, {
        "filter_version": FILTER_VERSION,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_bytes": len(raw), "source_entries": len(source),
        "filtered_sha256": hashlib.sha256(output).hexdigest(),
        "filtered_bytes": len(output), "filtered_entries": len(retained),
        "sport_id": 10, "family_entries": dict(sorted(counts.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-sha256", required=True)
    args = parser.parse_args()
    try:
        raw = _read_regular(args.source, MAX_SOURCE_BYTES, "MARKET_METADATA_INVALID")
        if hashlib.sha256(raw).hexdigest() != args.source_sha256:
            raise TournamentResearchError("SOURCE_SHA256_MISMATCH")
        output, provenance = filter_metadata(raw)
        # Exclusive private files. Existing outputs are never overwritten.
        _write_bytes(args.output, output)
        _write_bytes(args.output.with_name(args.output.name + ".provenance.json"),
                     (json.dumps(provenance, sort_keys=True) + "\n").encode())
    except TournamentResearchError as error:
        print(str(error))  # Only fixed error codes from the reviewed helpers.
        return 1
    print(json.dumps(provenance, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
