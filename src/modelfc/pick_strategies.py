"""Versioned independent strategies. No publication from GET, no legacy migration."""
from pathlib import Path
from modelfc.corner_analysis_store import _canonical_hash
from modelfc.ledger_storage import LedgerError, read_json_record, write_new_record

POLICY = "independent-value-strategies-v1"
DIRECTORY = "strategy-picks"
ACTIVATIONS = "strategy-policies"
STAKE_DOLLARS = 2


def publication_identity(offer, strategy):
    from modelfc.published_recommendations import _identity
    return _canonical_hash([POLICY, strategy, _identity(offer)])


def selection_identity(offer):
    from modelfc.published_recommendations import _identity
    # Same actual selection across strategies, independent of quote/book/time.
    return _canonical_hash([_identity(offer), offer.get("side"), offer.get("direction"), offer.get("line")])


def activate(state, lock_fd, now):
    """First writer invocation is the effective time, even if no picks qualify."""
    from modelfc.published_recommendations import _time
    from modelfc.research_predictions import _sync_directories
    directory = state / ACTIVATIONS
    if directory.is_symlink():
        raise LedgerError("INVALID_STRATEGY_ACTIVATION")
    directory.mkdir(exist_ok=True)
    path = directory / (_canonical_hash(POLICY) + ".json")
    if path.is_symlink():
        raise LedgerError("INVALID_STRATEGY_ACTIVATION")
    if path.exists():
        return effective_time(state, now)
    record = {"policy": POLICY, "effective_at_utc": now.isoformat()}
    record["record_hash"] = _canonical_hash(record)
    write_new_record(path, record, evidence_state=state, evidence_lock_fd=lock_fd)
    _sync_directories(directory, state)
    return record["effective_at_utc"]


def effective_time(state, now):
    from modelfc.published_recommendations import _time
    directory = Path(state) / ACTIVATIONS
    path = directory / (_canonical_hash(POLICY) + ".json")
    if directory.is_symlink() or path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
        raise LedgerError("INVALID_STRATEGY_ACTIVATION")
    record = read_json_record(path, "strategy activation", "UNKNOWN_STRATEGY_ACTIVATION")
    if (set(record) != {"policy", "effective_at_utc", "record_hash"}
            or record["policy"] != POLICY
            or record["record_hash"] != _canonical_hash({k:v for k,v in record.items() if k != "record_hash"})
            or _time(record["effective_at_utc"]) > now):
        raise LedgerError("INVALID_STRATEGY_ACTIVATION")
    return record["effective_at_utc"]


def _source_budget(state):
    """Fail explicitly at a bounded evidence budget; never truncate evidence silently."""
    import os
    roots = ("predictions", "prediction-targets", "opportunities", "market-observations",
             "analysis-outcomes", "btts-research", "btts-outcomes", "research-predictions")
    stack = [Path(state)/name for name in roots if (Path(state)/name).exists()]
    count = 0
    while stack:
        directory = stack.pop()
        if directory.is_symlink():
            raise LedgerError("INVALID_STRATEGY_EVIDENCE")
        with os.scandir(directory) as entries:
            for entry in entries:
                count += 1
                if count > 10000:
                    raise LedgerError("STRATEGY_EVIDENCE_CAPACITY_EXCEEDED")
                if entry.is_symlink():
                    raise LedgerError("INVALID_STRATEGY_EVIDENCE")
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.name.endswith(".json") and entry.stat(follow_symlinks=False).st_size > 1_000_000:
                    raise LedgerError("STRATEGY_EVIDENCE_CAPACITY_EXCEEDED")


def publish_locked(state_dir, *, include_btts=True, diagnostics=None, epl_approved_contracts=(), epl_only=False):
    from modelfc.published_recommendations import publish_locked as publish
    return publish(state_dir, include_btts=include_btts, independent=True, diagnostics=diagnostics,
                   epl_approved_contracts=epl_approved_contracts, epl_only=epl_only)


def read(state_dir, *, strategy="best_value", scope="current", market=None, competition=None, offset=0, limit=5, as_of=None):
    from modelfc import published_recommendations as published
    if (strategy not in published.VIEWS or scope not in {"current", "history"}
            or market not in {None, "TEAM_TOTAL", "BTTS"}
            or competition not in {None, "E1", "E0", "SP1"}
            or type(offset) is not int or not 0 <= offset <= 2000
            or type(limit) is not int or not 1 <= limit <= 20):
        raise ValueError("INVALID_STRATEGY_QUERY")
    now = as_of if as_of is not None else published._now()
    metadata = {"total": 0}
    def page(records):
        selected = [r for r in records if r.get("strategy") == strategy
            and (scope != "current" or published._time(r["offer"]["kickoff_utc"]) > now)
            and (market is None or r["offer"]["market_type"] == market)
            and (competition is None or r["offer"]["competition"] == competition)]
        selected.sort(key=lambda r:(published._time(r["published_at_utc"]), r["publication_id"]), reverse=True)
        metadata["total"] = len(selected)
        if selected:
            _source_budget(state_dir)
        return selected
    report = published.read(state_dir, directory_name=DIRECTORY, record_filter=page, as_of=as_of)
    totals = report["summary"]
    market_totals = report["by_league_market"]
    rows = report["publications"][offset:offset+limit]
    report = published._report(rows)
    for row in rows:
        row["simulated_stake_dollars"] = STAKE_DOLLARS
        row["profit_dollars"] = None if row["profit_units"] is None else row["profit_units"] * STAKE_DOLLARS
    return {"policy_version": POLICY, "strategy": strategy, "scope": scope,
        "stake_dollars": STAKE_DOLLARS, "simulated": True, "void_settlement_supported": False, "simulation_void_supported": True,
        "summary_scope": "page", "summary": report["summary"],
        "totals": totals, "market_totals": market_totals, "total_profit_dollars": totals["profit_units"] * STAKE_DOLLARS,
        "profit_dollars": report["summary"]["profit_units"] * STAKE_DOLLARS,
        "by_league_market": report["by_league_market"], "publications": rows,
        "total": metadata["total"], "offset": offset, "limit": limit,
        "next_offset": offset+len(rows) if offset+len(rows) < metadata["total"] else None}
