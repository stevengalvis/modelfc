"""Offline, deterministic core-market projection of an existing market cache.

No provider client, credentials, or production state are accessed. Selected
definitions are retained whole; an ID-only exclusion index preserves the
inventory's known-ID boundary without retaining unrelated definitions. Research
loader limits remain unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from modelfc.oddspapi_tournament_research import (
    MAX_METADATA_BYTES, MAX_METADATA_ROWS, TournamentResearchError,
    _classify_metadata, _metadata_index, _metadata_exclusion, _read_regular, _write_bytes,
    METADATA_FILTER_VERSION,
)

FILTER_VERSION = METADATA_FILTER_VERSION
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
        excluded = {"EXCLUDED_BY_ALLOWLIST": [], "UNSUPPORTED_FAMILY": []}
        for row in source:
            # Use the research validator even for removed rows; malformed or
            # colliding source identities must never disappear through filtering.
            _metadata_index([row])
            identity = row["marketId"]
            if identity <= 0 or identity in seen or type(row.get("sportId")) is not int:
                _reject()
            seen.add(identity)
            for field in ("marketType", "period", "marketName"):
                if (field in row and not isinstance(row[field], str)
                        and not (field == "period" and row[field] is None)):
                    _reject()
            if "playerProp" in row and type(row["playerProp"]) is not bool:
                _reject()
            for outcome in row["outcomes"]:
                if outcome["outcomeId"] <= 0:
                    _reject()
                if "outcomeName" in outcome and not isinstance(outcome["outcomeName"], str):
                    _reject()
            family = _classify_metadata(row)
            if family is not None:
                retained.append(row)
                counts[family] = counts.get(family, 0) + 1
            else:
                excluded[_metadata_exclusion(row)].append(identity)
        retained.sort(key=lambda row: row["marketId"])
        artifact = {
            "filter_version": FILTER_VERSION,
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "source_entries": len(source),
            "markets": retained,
            "excluded_ids": {reason: sorted(ids) for reason, ids in excluded.items()},
        }
        _metadata_index(artifact)
        output = (json.dumps(artifact, sort_keys=True, separators=(",", ":"),
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
        "excluded_entries": len(source) - len(retained),
        "excluded_reason_counts": {reason: len(ids) for reason, ids in excluded.items()},
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
