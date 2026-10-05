"""Installed trusted harness. PR modules are imported only inside run_capture()."""
import argparse
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import http.client
import importlib
import inspect
import io
import json
import math
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
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
    "OFFLINE_SCENARIO_INVALID", "MARKET_INTELLIGENCE_FAILED",
}
REASONS.update(f"PROVIDER_{endpoint}_{category}"
               for endpoint in ("FIXTURES", "MARKETS", "ODDS")
               for category in ("AUTH", "NOT_FOUND", "RATE_LIMIT", "SERVER", "MALFORMED", "OTHER"))
COUNTS = ("provider_request_count", "scenario_request_count", "selection_count", "team_total_count",
          "supported_team_total_count", "match_total_count", "gated_match_total_count",
          "analysis_batch_count", "replay_api_request_count", "prediction_count",
          "target_count", "opportunity_count")
FLAGS = ("immutable_capture_verified", "offline_replay_identical")
COMPONENTS = {
    "core_pipeline": {"PASS", "FAIL", "NOT_RUN"},
    "market_intelligence": {"PASS", "FAIL", "NOT_APPLICABLE", "NOT_RUN"},
    "offline_replay": {"PASS", "FAIL", "NOT_RUN"},
    "provider_compatibility": {"PASS", "FAIL", "BLOCKED", "NOT_RUN"},
}


def blank_report(sha, result="FAIL", reason="EXECUTION_ERROR", mode="OFFLINE"):
    return dict(result=result, mode=mode, commit_sha=sha, competition=None, fixture_id=None,
                home_team=None, away_team=None, kickoff_utc=None, capture_hash=None,
                cleanup_status="PENDING", reason=reason, credential_leakage_check=None,
                **{k: "NOT_RUN" for k in COMPONENTS},
                **{k: 0 for k in COUNTS}, **{k: False for k in FLAGS})


def checked_report(value, sha, secret, expected_mode=None):
    """Do not forward arbitrary keys, error strings, stdout or stderr."""
    template = blank_report(sha)
    if not isinstance(value, dict) or set(value) != set(template):
        raise ValueError("INVALID_REPORT")
    if (value["commit_sha"] != sha or value["result"] not in {"PASS", "FAIL", "BLOCKED"}
            or value["mode"] not in {"OFFLINE", "LIVE"}
            or expected_mode is not None and value["mode"] != expected_mode):
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
    for key, allowed in COMPONENTS.items():
        if value[key] not in allowed:
            raise ValueError("INVALID_REPORT")
    if value["credential_leakage_check"] is not None and type(value["credential_leakage_check"]) is not bool:
        raise ValueError("INVALID_REPORT")
    for k in ("fixture_id", "home_team", "away_team", "kickoff_utc", "capture_hash"):
        if value[k] is not None and (not isinstance(value[k], str) or len(value[k]) > 160
                                   or any(ord(c) < 32 for c in value[k])):
            raise ValueError("INVALID_REPORT")
    secrets = (secret,) if isinstance(secret, str) else tuple(secret)
    if any(item and item in json.dumps(value, ensure_ascii=False) for item in secrets):
        raise ValueError("SECURITY_ERROR")
    # Mode and component consistency apply to failed/blocked reports too.
    if value["mode"] == "OFFLINE":
        if (value["provider_compatibility"] != "NOT_RUN"
                or value["provider_request_count"] != 0 or value["result"] == "BLOCKED"):
            raise ValueError("INVALID_REPORT")
    elif (value["provider_request_count"] > 3 or value["scenario_request_count"] != 0
          or value["market_intelligence"] != "NOT_RUN"):
        raise ValueError("INVALID_REPORT")
    if (value["result"] != "FAIL" and ("FAIL" in (value[k] for k in COMPONENTS)
            or value["cleanup_status"] == "FAILED" or value["credential_leakage_check"] is False)):
        raise ValueError("INVALID_REPORT")
    if ((value["reason"] == "COMPLETE") != (value["result"] == "PASS")
            or value["provider_compatibility"] == "BLOCKED" and value["result"] == "PASS"
            or value["core_pipeline"] == "PASS" and not value["immutable_capture_verified"]
            or (value["offline_replay"] == "PASS") != value["offline_replay_identical"]
            or value["offline_replay"] == "PASS" and value["replay_api_request_count"] != 0):
        raise ValueError("INVALID_REPORT")
    if value["result"] == "BLOCKED" and (value["reason"] not in {
            "NO_ELIGIBLE_FIXTURE", "NO_TEAM_TOTALS", "INSUFFICIENT_HISTORY", "HISTORY_UNAVAILABLE"}
            or value["provider_compatibility"] not in {"BLOCKED", "NOT_RUN"}
            or any(value[k] != "NOT_RUN" for k in ("core_pipeline", "offline_replay", "market_intelligence"))):
        raise ValueError("INVALID_REPORT")
    if ((value["market_intelligence"] in {"PASS", "FAIL", "NOT_APPLICABLE"}
            or value["provider_compatibility"] == "PASS")
            and (value["core_pipeline"] != "PASS" or value["offline_replay"] != "PASS")):
        raise ValueError("INVALID_REPORT")
    if value["result"] == "PASS":
        if (value["reason"] != "COMPLETE" or value["core_pipeline"] != "PASS"
                or value["offline_replay"] != "PASS" or not all(value[k] for k in FLAGS[:2])
                or value["supported_team_total_count"] < 1
                or value["replay_api_request_count"] != 0 or not value["capture_hash"]):
            raise ValueError("INVALID_REPORT")
        if value["mode"] == "OFFLINE":
            if (value["provider_compatibility"] != "NOT_RUN"
                    or value["provider_request_count"] != 0
                    or value["scenario_request_count"] != 3
                    or value["market_intelligence"] not in {"PASS", "NOT_APPLICABLE"}):
                raise ValueError("INVALID_REPORT")
        elif value["provider_compatibility"] != "PASS" or value["provider_request_count"] != 3:
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


OFFLINE_NOW = datetime(2026, 9, 20, 9, tzinfo=timezone.utc)
OFFLINE_FIXTURE = {
    "fixtureId": "id1000001872339860", "participant1Id": 3, "participant2Id": 8,
    "sportId": 10, "tournamentId": 18, "statusId": 0,
    "startTime": "2026-09-20T11:00:00.000Z",
    "participant1Name": "Wolverhampton Wanderers",
    "participant2Name": "West Bromwich Albion",
    "categorySlug": "england", "tournamentSlug": "championship",
}
OFFLINE_MARKETS = (
    (10799, "Corners - Over Under Full Time", "totals-corners", 8.5,
     ((10799, "Over"), (10800, "Under"))),
    (101432, "Corners - Over Under Team 1", "teamtotals-corners-team1", 5.5,
     ((101432, "Over"), (101433, "Under"))),
    (101484, "Corners - Over Under Team 2", "teamtotals-corners-team2", 3.5,
     ((101484, "Over"), (101485, "Under"))),
)
OFFLINE_PRICES = (
    ("draftkings", 10799, 10800, 2.4, "140"),
    ("draftkings", 10799, 10799, 1.513, "-195"),
    ("draftkings", 101432, 101432, 1.87, "-115"),
    ("draftkings", 101432, 101433, 1.833, "-120"),
    ("draftkings", 101484, 101484, 1.69, "-145"),
    ("draftkings", 101484, 101485, 2.05, "105"),
    ("fanduel", 10799, 10799, 1.54, "-185"),
    ("fanduel", 10799, 10800, 2.38, "138"),
    ("fanduel", 101432, 101432, 1.91, "-110"),
    ("fanduel", 101432, 101433, 1.83, "-120"),
    ("fanduel", 101484, 101484, 1.74, "-135"),
    ("fanduel", 101484, 101485, 2.04, "104"),
)


def offline_responses():
    """Trusted subset of the sanitized recordings used by test_oddspapi."""
    metadata = [{
        "marketId": mid, "marketName": name, "marketType": family,
        "period": "fulltime", "handicap": line, "sportId": 10,
        "playerProp": False,
        "outcomes": [{"outcomeId": oid, "outcomeName": direction}
                     for oid, direction in outcomes],
    } for mid, name, family, line, outcomes in OFFLINE_MARKETS]
    odds = dict(OFFLINE_FIXTURE)
    odds["bookmakerOdds"] = {
        book: {"bookmakerIsActive": True, "suspended": False, "markets": {}}
        for book in ("draftkings", "fanduel")
    }
    for book, market_id, outcome_id, decimal, american in OFFLINE_PRICES:
        markets = odds["bookmakerOdds"][book]["markets"]
        market = markets.setdefault(str(market_id), {"marketActive": True, "outcomes": {}})
        market["outcomes"][str(outcome_id)] = {"players": {"0": {
            "active": True, "price": decimal, "priceAmerican": american,
            "mainLine": True, "changedAt": "2026-09-20T08:55:00Z",
            "bookmakerChangedAt": "2026-09-20T08:55:00Z",
        }}}
    return {"fixtures": [dict(OFFLINE_FIXTURE)], "markets": metadata, "odds": odds}


class OfflineOpener:
    """Exact recorded request boundary. It has no socket or relay capability."""
    def __init__(self):
        self.calls = []

    def open(self, request, timeout=30):
        url = urlsplit(request.full_url)
        params = parse_qsl(url.query, keep_blank_values=True)
        if (url.scheme, url.netloc, url.fragment) != ("https", "api.oddspapi.io", ""):
            raise ValueError("OFFLINE_SCENARIO_INVALID")
        if sum(key == "apiKey" for key, _ in params) != 1 or any(
                value for key, value in params if key == "apiKey"):
            raise ValueError("SECURITY_ERROR")
        values = dict((key, value) for key, value in params if key != "apiKey")
        endpoint = url.path.rsplit("/", 1)[-1]
        expected = {
            "fixtures": {"tournamentId": "18", "statusId": "0", "language": "en",
                         "bookmakers": "draftkings,fanduel", "from": "2026-09-20T00:00:00Z",
                         "to": "2026-09-21T00:00:00Z"},
            "markets": {"language": "en"},
            "odds": {"fixtureId": OFFLINE_FIXTURE["fixtureId"],
                     "bookmakers": "draftkings,fanduel", "verbosity": "3",
                     "language": "en", "oddsFormat": "american"},
        }
        if (endpoint not in expected or values != expected[endpoint]
                or url.path != "/v4/" + endpoint or len(values) != len(params) - 1
                or self.calls + [endpoint] != ["fixtures", "markets", "odds"][:len(self.calls) + 1]):
            raise ValueError("OFFLINE_SCENARIO_INVALID")
        self.calls.append(endpoint)
        body = io.BytesIO(json.dumps(offline_responses()[endpoint]).encode())
        body.headers = {}
        return body


def validation_client(provider, competition, opener=None):
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
        client._key, client._opener = "", opener or RelayOpener()
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


def evidence_snapshot(state):
    return {str(path.relative_to(state)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in state.rglob("*") if path.is_file()}


def market_intelligence_component(state, prediction, quotes, now):
    """Exercise the optional candidate interface against trusted observations."""
    try:
        intelligence = importlib.import_module("modelfc.corner_market_intelligence")
    except ModuleNotFoundError as error:
        if error.name == "modelfc.corner_market_intelligence":
            return "NOT_APPLICABLE"
        raise
    from modelfc import corner_opportunities as evidence
    from modelfc.corner_market_data import CornerMarketObservation, MarketFixture
    from modelfc.corner_markets import american_odds_terms

    interface = getattr(intelligence, "get_market_intelligence", None)
    if not callable(interface):
        raise AssertionError("missing market intelligence interface")
    base = interface(state, prediction["prediction_id"], as_of=now)
    cross_book = [target for target in base["targets"]
                  if {offer["bookmaker"] for offer in target["offers"]} == {"draftkings", "fanduel"}
                  and all(offer["status"] == "SUPPORTED" for offer in target["offers"])]
    assert cross_book
    target = cross_book[0]
    expected = sorted(target["offers"], key=lambda offer: (-offer["decimal_odds"], offer["bookmaker"]))[0]
    assert target["latest_observed_best"]["best_bookmaker"] == expected["bookmaker"]
    assert target["latest_observed_best"]["best_decimal_odds"] == expected["decimal_odds"]

    qualified = {(item["target_id"], item["offer"]["bookmaker"])
                 for item in evidence.opportunity_records(state, prediction["prediction_id"])}
    reported = {(offer["target_id"], offer["bookmaker"])
                for offer in base["research_ranked_offers"] if offer["qualified"]}
    assert reported == qualified
    assert all(offer["expected_profit"] > 0 for offer in base["recommendations"])
    assert any(offer["market_type"] == "MATCH_TOTAL" and offer["status"] == "UNSUPPORTED"
               for target in base["targets"] for offer in target["offers"])
    assert all(offer["market_type"] != "MATCH_TOTAL" for offer in base["recommendations"])
    assert base["research_ranked_offers"]

    stale = interface(state, prediction["prediction_id"], as_of=now + timedelta(seconds=301))
    assert stale["research_ranked_offers"] and not stale["current_eligible_offers"]
    assert not stale["recommendations"]

    fixture = MarketFixture(
        "E1", prediction["fixture"]["home_team"], prediction["fixture"]["away_team"],
        datetime.fromisoformat(prediction["fixture"]["kickoff_at"]), "oddspapi",
        prediction["fixture"]["provider_fixture_id"], dict(quotes.fixture),
    )
    unavailable_at = (now + timedelta(seconds=60)).isoformat()
    unavailable = CornerMarketObservation(
        fixture, (), {"fanduel": {"status": "BOOKMAKER_UNUSABLE", "families": {}, "issues": []}},
        SimpleNamespace(retrieved_at=unavailable_at),
    )
    evidence.store_market_observation(state, unavailable)
    unavailable_view = interface(state, prediction["prediction_id"], as_of=now + timedelta(seconds=60))
    fd = next(offer for offer in unavailable_view["research_ranked_offers"]
              if offer["bookmaker"] == "fanduel")
    dk = next(offer for offer in unavailable_view["research_ranked_offers"]
              if offer["bookmaker"] == "draftkings")
    assert fd["current_availability"] == "UNAVAILABLE" and not fd["current_eligible"]
    assert dk["current_availability"] == "UNKNOWN" and not dk["current_eligible"]
    assert fd not in unavailable_view["recommendations"]

    unknown_at = (now + timedelta(seconds=120)).isoformat()
    unknown = CornerMarketObservation(
        fixture, (), {"fanduel": {"status": "BOOKMAKER_UNAVAILABLE", "families": {}, "issues": []}},
        SimpleNamespace(retrieved_at=unknown_at),
    )
    evidence.store_market_observation(state, unknown)
    unknown_view = interface(state, prediction["prediction_id"], as_of=now + timedelta(seconds=120))
    fd = next(offer for offer in unknown_view["research_ranked_offers"]
              if offer["bookmaker"] == "fanduel")
    assert fd["current_availability"] == "UNKNOWN" and not fd["current_eligible"]

    template = next(selection for selection in quotes.selections
                    if selection.request.market_type == "TEAM_TOTAL"
                    and selection.request.team_side == "HOME")
    negative_at = (now + timedelta(seconds=180)).isoformat()
    negative = []
    for line in (4.5, 5.0, 5.5, 6.0, 6.5):
        for direction, odds in (("UNDER", -200), ("OVER", -1000)):
            identity = f"trusted-negative-{line}-{direction.lower()}"
            negative.append(replace(
                template, bookmaker="fanduel", market_id=f"trusted-negative-home-{line}",
                outcome_id=direction.lower(), decimal_odds=1 + american_odds_terms(odds)[0],
                retrieved_at=negative_at,
                request=replace(template.request, client_market_id=identity,
                                side=direction, line=line, american_odds=odds),
            ))
    available = {"fanduel": {"status": "CORNERS_RETURNED", "families": {}, "issues": []}}
    evidence.store_market_observation(
        state, CornerMarketObservation(fixture, tuple(negative), available,
                                       SimpleNamespace(retrieved_at=negative_at)),
    )
    negative_view = interface(state, prediction["prediction_id"], as_of=now + timedelta(seconds=180))
    negative_offers = [offer for offer in negative_view["research_ranked_offers"]
                       if offer["qualified"] and offer["current_eligible"]
                       and offer["expected_profit"] <= 0]
    assert negative_offers
    assert all(not offer["recommendation_eligible"] for offer in negative_offers)
    assert all(offer not in negative_view["recommendations"] for offer in negative_offers)

    before = evidence_snapshot(state)
    first = interface(state, prediction["prediction_id"], as_of=now + timedelta(seconds=180))
    second = interface(state, prediction["prediction_id"], as_of=now + timedelta(seconds=180))
    assert first == second and evidence_snapshot(state) == before
    return "PASS"


def run_capture(sha, snapshot=Path("/history"), output=Path("/output"), mode="OFFLINE"):
    mode = mode.upper()
    if mode not in {"OFFLINE", "LIVE"}:
        return blank_report(sha, reason="INVALID_CONFIGURATION", mode="OFFLINE")
    report = blank_report(sha, mode=mode)
    # Run with -I. Add only the exact exported PR's Python source, never its ops/.
    from modelfc.providers import oddspapi as provider
    from modelfc import corner_analysis_store as store
    from modelfc import corner_opportunities as opportunities
    provider.configured_history_lock = snapshot_lock
    store.configured_history_lock = snapshot_lock
    # Git metadata is supplied by the controller's verified export, not a PR .git directory.
    store.git_commit_sha = lambda: sha
    manifest = json.loads((snapshot / "manifest.json").read_text())
    config_path = snapshot / "corner_data.json"
    calls, replay_calls = [], []
    original_analyze = provider.analyze_corner_markets
    original_get = provider.OddsPapiClient._get
    original_now = provider._now
    original_timestamp = provider.utc_timestamp
    batches, metadata_cache = [], {}
    replaying = False
    now = OFFLINE_NOW if mode == "OFFLINE" else datetime.now(timezone.utc)
    opener = OfflineOpener() if mode == "OFFLINE" else RelayOpener()

    @wraps(original_get)
    def metered(client, endpoint, **params):
        if replaying:
            replay_calls.append(endpoint)
            raise AssertionError("offline replay made a provider request")
        calls.append(endpoint)
        if endpoint == "markets" and endpoint in metadata_cache:
            return deepcopy(metadata_cache[endpoint])
        value = original_get(client, endpoint, **params)
        if endpoint == "markets":
            metadata_cache[endpoint] = deepcopy(value)
        return value

    def analyzed(observations, fixture, markets, **settings):
        assert not replaying and 0 < len(markets) <= 32
        assert tuple(settings["available_market_types"]) == ("TEAM_TOTAL",)
        if batches:
            assert observations is batches[0][0] and settings == batches[0][1]
        batches.append((observations, settings))
        return original_analyze(observations, fixture, markets, **settings)

    provider.analyze_corner_markets = analyzed
    provider.OddsPapiClient._get = metered
    provider._now = lambda: now
    provider.utc_timestamp = lambda: now.isoformat()
    saw_fixture = False
    try:
        if "E1" not in manifest["leagues"]:
            raise ValueError("OFFLINE_SCENARIO_INVALID" if mode == "OFFLINE" else "HISTORY_UNAVAILABLE")
        client = validation_client(provider, "E1", opener)
        fixtures = client.fixtures(now.date())
        for fixture in fixtures[:1]:
            saw_fixture = True
            quotes = client.quotes(fixture)
            team_count = sum(s.request.market_type == "TEAM_TOTAL" for s in quotes.selections)
            if not team_count:
                continue
            assert calls == ["fixtures", "markets", "odds"]
            if mode == "OFFLINE":
                assert opener.calls == ["fixtures", "markets", "odds"]
            report.update(competition="E1", fixture_id=fixture["fixtureId"],
                          home_team=fixture["participant1Name"], away_team=fixture["participant2Name"],
                          kickoff_utc=fixture["startTime"])
            try:
                response, created = provider.capture_quotes(
                    quotes, data_config_path=config_path, state_dir=output / "state",
                    capture_key="vps-validation-" + sha)
            except ValueError as error:
                if str(error).startswith(("insufficient history", "insufficient home history", "insufficient away history")):
                    report.update(result="BLOCKED" if mode == "LIVE" else "FAIL",
                                  reason="INSUFFICIENT_HISTORY",
                                  provider_compatibility="BLOCKED" if mode == "LIVE" else "NOT_RUN")
                    return report
                raise
            assert created
            state = output / "state"
            path = state / "analyses" / (response["analysis_id"] + ".json")
            assert len(list(path.parent.glob("*.json"))) == 1
            capture_bytes = path.read_bytes()
            record = json.loads(capture_bytes)
            verify_capture(record, response, quotes, sha, snapshot)
            prediction, prediction_created = opportunities.store_prediction_from_capture(
                state, response["analysis_id"])
            observation, _ = opportunities.store_observation_from_capture(state, response["analysis_id"])
            assessment = opportunities.assess_observation(state, prediction, observation)
            assert prediction_created and assessment["targets"] == len(quotes.selections)
            before_replay = evidence_snapshot(state)
            replaying = True
            replay, recreated = provider.capture_quotes(
                quotes, data_config_path=config_path, state_dir=state,
                capture_key="vps-validation-" + sha)
            replay_prediction, prediction_recreated = opportunities.store_prediction_from_capture(
                state, response["analysis_id"])
            replay_observation, observation_recreated = opportunities.store_observation_from_capture(
                state, response["analysis_id"])
            replay_assessment = opportunities.assess_observation(state, replay_prediction, replay_observation)
            assert not recreated and replay == response and not prediction_recreated and not observation_recreated
            assert replay_assessment["opportunities_created"] == 0
            assert store.load_analysis(state, response["analysis_id"]) == response
            assert path.read_bytes() == capture_bytes and evidence_snapshot(state) == before_replay
            assert not replay_calls
            replaying = False
            match_count = len(quotes.selections) - team_count
            targets = len(list((state / "prediction-targets").rglob("*.json")))
            opportunities_count = len(opportunities.opportunity_records(state, prediction["prediction_id"]))
            report.update(
                core_pipeline="PASS", offline_replay="PASS",
                provider_compatibility="PASS" if mode == "LIVE" else "NOT_RUN",
                selection_count=len(quotes.selections), team_total_count=team_count,
                supported_team_total_count=team_count, match_total_count=match_count,
                gated_match_total_count=match_count, analysis_batch_count=len(batches),
                prediction_count=1, target_count=targets, opportunity_count=opportunities_count,
                capture_hash=hashlib.sha256(capture_bytes).hexdigest(),
                immutable_capture_verified=True, offline_replay_identical=True,
            )
            market_component = "NOT_RUN"
            if mode == "OFFLINE":
                try:
                    market_component = market_intelligence_component(state, prediction, quotes, now)
                except BaseException:
                    report.update(result="FAIL", reason="MARKET_INTELLIGENCE_FAILED",
                                  market_intelligence="FAIL")
                    return report
            report.update(
                result="PASS", reason="COMPLETE", market_intelligence=market_component,
            )
            return report
        report.update(result="BLOCKED" if mode == "LIVE" else "FAIL",
                      reason="NO_TEAM_TOTALS" if saw_fixture else "NO_ELIGIBLE_FIXTURE",
                      provider_compatibility="BLOCKED" if mode == "LIVE" else "NOT_RUN")
        return report
    except provider.OddsPapiError:
        report.update(result="FAIL", reason="PROVIDER_ERROR", core_pipeline="FAIL",
                      provider_compatibility="FAIL" if mode == "LIVE" else "NOT_RUN")
        return report
    except AssertionError:
        report.update(result="FAIL", reason="ASSERTION_FAILED", core_pipeline="FAIL",
                      provider_compatibility="FAIL" if mode == "LIVE" else "NOT_RUN")
        return report
    except Exception as error:
        reason = str(error) if isinstance(error, ValueError) and str(error) in REASONS else "EXECUTION_ERROR"
        report.update(result="FAIL", reason=reason, core_pipeline="FAIL",
                      provider_compatibility="FAIL" if mode == "LIVE" else "NOT_RUN")
        return report
    finally:
        report["provider_request_count"] = len(calls) if mode == "LIVE" else 0
        report["scenario_request_count"] = len(opener.calls) if mode == "OFFLINE" else 0
        report["replay_api_request_count"] = len(replay_calls)
        provider.OddsPapiClient._get = original_get
        provider.analyze_corner_markets = original_analyze
        provider._now = original_now
        provider.utc_timestamp = original_timestamp


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sha", required=True)
    parser.add_argument("--mode", choices=("offline", "live"), default="offline")
    args = parser.parse_args()
    report_fd = os.dup(1)
    # Suppress PR stdout/stderr at OS descriptor level, including child processes.
    with open(os.devnull, "w") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    sys.path.insert(0, "/subject/src")
    report = blank_report(args.sha, mode=args.mode.upper())
    try:
        report = run_capture(args.sha, mode=args.mode)
    except BaseException:
        pass
    os.write(report_fd, json.dumps(report).encode())
    os.close(report_fd)


if __name__ == "__main__":
    main()
