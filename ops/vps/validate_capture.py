"""Installed trusted harness. PR modules are imported only inside run_capture()."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import http.client
import inspect
import io
import json
import math
import os
from pathlib import Path
import socket
import sys
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit
from functools import wraps

REASONS = {
    "COMPLETE", "NO_ELIGIBLE_FIXTURE", "NO_TEAM_TOTALS", "INSUFFICIENT_HISTORY",
    "PROVIDER_ERROR", "ASSERTION_FAILED", "EXECUTION_ERROR", "SECURITY_ERROR",
    "WRONG_SHA", "REPOSITORY_MISMATCH", "PR_NOT_OPEN", "TIMEOUT",
    "REQUEST_BUDGET_EXCEEDED", "CLEANUP_FAILED", "BUSY", "INVALID_CONFIGURATION",
    "SOURCE_ERROR", "INVALID_REPORT", "HISTORY_UNAVAILABLE",
    "CLIENT_INTERFACE_CHANGED",
}
COUNTS = ("api_request_count", "selection_count", "team_total_count",
          "supported_team_total_count", "match_total_count", "gated_match_total_count",
          "analysis_batch_count", "replay_api_request_count")
FLAGS = ("immutable_capture_verified", "offline_replay_identical", "credential_leakage_check")


def blank_report(sha, result="FAIL", reason="EXECUTION_ERROR"):
    return dict(result=result, commit_sha=sha, competition=None, fixture_id=None,
                home_team=None, away_team=None, kickoff_utc=None, capture_hash=None,
                cleanup_status="PENDING", reason=reason,
                **{k: 0 for k in COUNTS}, **{k: False for k in FLAGS})


def checked_report(value, sha, secret):
    """Do not forward arbitrary keys, error strings, stdout or stderr."""
    template = blank_report(sha)
    if not isinstance(value, dict) or set(value) != set(template):
        raise ValueError("INVALID_REPORT")
    if value["commit_sha"] != sha or value["result"] not in {"PASS", "FAIL", "BLOCKED"}:
        raise ValueError("INVALID_REPORT")
    if value["reason"] not in REASONS or value["competition"] not in {None, "E1", "SP1"}:
        raise ValueError("INVALID_REPORT")
    if value["cleanup_status"] not in {"PENDING", "COMPLETE", "FAILED"}:
        raise ValueError("INVALID_REPORT")
    for k in COUNTS:
        if type(value[k]) is not int or not 0 <= value[k] <= 100000:
            raise ValueError("INVALID_REPORT")
    for k in FLAGS:
        if type(value[k]) is not bool:
            raise ValueError("INVALID_REPORT")
    for k in ("fixture_id", "home_team", "away_team", "kickoff_utc", "capture_hash"):
        if value[k] is not None and (not isinstance(value[k], str) or len(value[k]) > 160
                                   or any(ord(c) < 32 for c in value[k])):
            raise ValueError("INVALID_REPORT")
    if secret and secret in json.dumps(value, ensure_ascii=False):
        raise ValueError("SECURITY_ERROR")
    if value["result"] == "PASS" and (value["reason"] != "COMPLETE"
            or not all(value[k] for k in FLAGS[:2]) or value["supported_team_total_count"] < 1
            or value["replay_api_request_count"] != 0 or not value["capture_hash"]):
        raise ValueError("INVALID_REPORT")
    return value


class RelayConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect("/relay/api.sock")


class RelayOpener:
    def open(self, request, timeout=30):
        url = urlsplit(request.full_url)
        if url.scheme != "https" or url.netloc != "api.oddspapi.io" or url.fragment:
            raise ValueError("SECURITY_ERROR")
        params = parse_qsl(url.query, keep_blank_values=True)
        # Production _get adds an empty key. Never send authentication to the relay.
        if sum(k == "apiKey" for k, _ in params) != 1 or any(v for k, v in params if k == "apiKey"):
            raise ValueError("SECURITY_ERROR")
        params = [(k, v) for k, v in params if k != "apiKey"]
        connection = RelayConnection("localhost", timeout=timeout)
        try:
            connection.request("GET", url.path + "?" + urlencode(params))
            response = connection.getresponse()
            body = response.read(8 * 1024 * 1024 + 1)
            if len(body) > 8 * 1024 * 1024:
                raise ValueError("PROVIDER_ERROR")
            if response.status >= 400:
                raise HTTPError("redacted", response.status, "provider error", response.headers, io.BytesIO(body))
            stream = io.BytesIO(body)
            stream.headers = response.headers
            return stream
        finally:
            connection.close()


def validation_client(provider, competition):
    """Validation-only factory; production constructor and HTTPS are not exercised."""
    if "ODDSPAPI_API_KEY" in os.environ:
        raise ValueError("SECURITY_ERROR")
    expected = {"__init__": ("self", "competition"),
                "_get": ("self", "endpoint", "_fixture_discovery", "params"),
                "fixtures": ("self", "day"), "quotes": ("self", "fixture")}
    try:
        cls = provider.OddsPapiClient
        if any(tuple(inspect.signature(getattr(cls, name)).parameters) != params
               for name, params in expected.items()):
            raise ValueError
        client = object.__new__(cls)
        client.config = provider.COMPETITIONS[competition]
        client._key, client._opener = "", RelayOpener()
        client._last_request, client.requests, client.usage_headers = None, 0, {}
        return client
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ValueError("CLIENT_INTERFACE_CHANGED") from None


@contextmanager
def snapshot_lock(config):
    # Same flock protocol as configured_history_lock; snapshot is already immutable.
    with (config.directory / "data/corner-refresh/refresh.lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        yield


def verify_capture(record, response, quotes, sha, snapshot):
    from modelfc.corner_analysis_store import _canonical_hash
    assert record["request_hash"] == _canonical_hash(record["request"])
    assert record["response_hash"] == _canonical_hash(response)
    assert record["response"] == response
    assert response["forecast"]["model_version"] == sha
    fixture = response["fixture"]
    kickoff = datetime.fromisoformat(fixture["kickoff_at"])
    assert datetime.fromisoformat(response["created_at"]) < kickoff
    assert kickoff == datetime.fromisoformat(quotes.fixture["startTime"].replace("Z", "+00:00"))
    assert record["request"]["prematch"]["fixture"]["fixtureId"] == quotes.fixture["fixtureId"]
    from dataclasses import asdict
    assert record["request"]["prematch"]["selections"] == [asdict(s) for s in quotes.selections]
    assert record["request"]["prematch"]["availability"] == quotes.availability
    assert len(response["markets"]) == len(quotes.selections)
    assert response["forecast"]["source_data_hashes"]
    for source in response["forecast"]["source_data_hashes"]:
        assert Path(source["filename"]).name == source["filename"]
        assert hashlib.sha256((snapshot / source["filename"]).read_bytes()).hexdigest() == source["sha256"]
    for market in response["markets"]:
        if market["market_type"] == "MATCH_TOTAL":
            assert market["unsupported_reason"] == "HISTORICAL_EVALUATION_REQUIRED"
            assert market["status"] == "UNSUPPORTED"
        elif market["market_type"] == "TEAM_TOTAL":
            assert market["status"] == "SUPPORTED"
            for field in ("model_probability", "implied_probability", "probability_edge", "expected_profit"):
                assert isinstance(market[field], (int, float)) and math.isfinite(market[field])


def run_capture(sha, snapshot=Path("/history"), output=Path("/output"), days=3):
    report = blank_report(sha)
    # Run with -I. Add only the exact exported PR's Python source, never its ops/.
    from modelfc.providers import oddspapi as provider
    from modelfc import corner_analysis_store as store
    provider.configured_history_lock = snapshot_lock
    store.configured_history_lock = snapshot_lock
    # Git metadata is supplied by the controller's verified export, not a PR .git directory.
    store.git_commit_sha = lambda: sha
    manifest = json.loads((snapshot / "manifest.json").read_text())
    config_path = snapshot / "corner_data.json"
    calls, replay_calls = [], []
    original_analyze = provider.analyze_corner_markets
    batches = []
    original_get = provider.OddsPapiClient._get
    replaying = False

    @wraps(original_get)
    def metered(client, endpoint, **params):
        if replaying:
            replay_calls.append(endpoint)
            raise AssertionError("offline replay made an API request")
        calls.append(endpoint)
        return original_get(client, endpoint, **params)

    def analyzed(observations, fixture, markets, **settings):
        assert not replaying and 0 < len(markets) <= 32
        assert tuple(settings["available_market_types"]) == ("TEAM_TOTAL",)
        if batches:
            assert observations is batches[0][0] and settings == batches[0][1]
        batches.append((observations, settings))
        return original_analyze(observations, fixture, markets, **settings)

    provider.analyze_corner_markets = analyzed
    provider.OddsPapiClient._get = metered
    saw_fixture = False
    try:
        for offset in range(days):
            for competition in ("E1", "SP1"):
                if competition not in manifest["leagues"]:
                    continue
                client = validation_client(provider, competition)
                fixtures = client.fixtures((datetime.now(timezone.utc) + timedelta(days=offset)).date())
                for fixture in fixtures[:1]:
                    saw_fixture = True
                    quotes = client.quotes(fixture)
                    team_count = sum(s.request.market_type == "TEAM_TOTAL" for s in quotes.selections)
                    if not team_count:
                        continue
                    report.update(competition=competition, fixture_id=fixture["fixtureId"],
                                  home_team=fixture["participant1Name"], away_team=fixture["participant2Name"],
                                  kickoff_utc=fixture["startTime"])
                    try:
                        response, created = provider.capture_quotes(
                            quotes, data_config_path=config_path, state_dir=output / "state",
                            capture_key="vps-validation-" + sha)
                    except ValueError as error:
                        if str(error).startswith(("insufficient history", "insufficient home history", "insufficient away history")):
                            report.update(result="BLOCKED", reason="INSUFFICIENT_HISTORY")
                            return report
                        raise
                    assert created
                    path = output / "state/analyses" / (response["analysis_id"] + ".json")
                    assert len(list(path.parent.glob("*.json"))) == 1
                    before = path.read_bytes()
                    record = json.loads(before)
                    verify_capture(record, response, quotes, sha, snapshot)
                    replaying = True
                    replay, recreated = provider.capture_quotes(
                        quotes, data_config_path=config_path, state_dir=output / "state",
                        capture_key="vps-validation-" + sha)
                    assert not recreated and replay == response
                    assert store.load_analysis(output / "state", response["analysis_id"]) == response
                    assert path.read_bytes() == before
                    assert not replay_calls
                    report.update(result="PASS", reason="COMPLETE", selection_count=len(quotes.selections),
                                  team_total_count=team_count, supported_team_total_count=team_count,
                                  match_total_count=len(quotes.selections)-team_count,
                                  gated_match_total_count=len(quotes.selections)-team_count,
                                  analysis_batch_count=len(batches),
                                  capture_hash=hashlib.sha256(before).hexdigest(),
                                  immutable_capture_verified=True, offline_replay_identical=True)
                    return report
        report.update(result="BLOCKED", reason="NO_TEAM_TOTALS" if saw_fixture else "NO_ELIGIBLE_FIXTURE")
        return report
    except provider.OddsPapiError:
        report.update(result="FAIL", reason="PROVIDER_ERROR")
        return report
    except AssertionError:
        report.update(result="FAIL", reason="ASSERTION_FAILED")
        return report
    except Exception as error:
        reason = str(error) if isinstance(error, ValueError) and str(error) in REASONS else "EXECUTION_ERROR"
        report.update(result="FAIL", reason=reason)
        return report
    finally:
        report["api_request_count"] = len(calls)
        report["replay_api_request_count"] = len(replay_calls)
        provider.OddsPapiClient._get = original_get
        provider.analyze_corner_markets = original_analyze


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    report_fd = os.dup(1)
    # Suppress PR stdout/stderr at OS descriptor level, including child processes.
    with open(os.devnull, "w") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    sys.path.insert(0, "/subject/src")
    report = blank_report(args.sha)
    try:
        report = run_capture(args.sha)
    except BaseException:
        pass
    os.write(report_fd, json.dumps(report).encode())
    os.close(report_fd)


if __name__ == "__main__":
    main()
