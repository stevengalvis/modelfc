"""Durable storage for immutable upcoming-fixture provider snapshots."""

from pathlib import Path
from typing import Any

from modelfc.ledger_storage import (
    LedgerError,
    ensure_directory,
    ledger_lock,
    read_json_record,
    write_new_record,
)
from modelfc.upcoming_fixtures import FixtureSnapshot


def _snapshot_path(cache_dir: Path, snapshot_id: str) -> Path:
    if not isinstance(snapshot_id, str) or not snapshot_id.strip():
        raise ValueError("snapshot_id must be non-empty text")
    return cache_dir / "snapshots" / f"{snapshot_id}.json"


def save_fixture_snapshot(cache_dir: str | Path, snapshot: FixtureSnapshot) -> Path:
    """Persist a snapshot once; never overwrite an existing snapshot."""

    root = Path(cache_dir)
    ensure_directory(root, "fixture cache")
    path = _snapshot_path(root, snapshot.provider_snapshot_id)
    ensure_directory(path.parent, "fixture snapshot directory")
    with ledger_lock(root):
        if path.exists():
            existing = load_fixture_snapshot(root, snapshot.provider_snapshot_id)
            if existing != snapshot:
                raise LedgerError("fixture snapshot ID already exists with different contents")
            return path
        write_new_record(path, snapshot.to_dict())
    return path


def load_fixture_snapshot(cache_dir: str | Path, snapshot_id: str) -> FixtureSnapshot:
    root = Path(cache_dir)
    record = read_json_record(
        _snapshot_path(root, snapshot_id), "fixture snapshot",
        f"unknown fixture snapshot: {snapshot_id}",
    )
    try:
        return FixtureSnapshot.from_dict(record)
    except (TypeError, ValueError) as error:
        raise LedgerError(f"invalid fixture snapshot {snapshot_id}: {error}") from error


def snapshot_record(snapshot: FixtureSnapshot) -> dict[str, Any]:
    """Return the versioned JSON record used by tests and API layers."""

    return snapshot.to_dict()
