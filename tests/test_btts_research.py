"""Synthetic offline BTTS model, evidence and API regressions. No provider calls."""

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from modelfc.btts_market_data import BttsFixture, BttsObservation, BttsSelection, BookAvailability
from modelfc.btts_model import (
    DEEPFC_SOURCE_COMMIT, GoalHistory, GoalResult, btts_probabilities, freeze_btts_forecast,
    history_from_bytes, load_goal_history,
)
from modelfc.btts_research import (
    ResearchRecord, comparisons_from_snapshot, digest, make_record, paired_values,
    read_btts_research, record_btts_research, research_views,
)
from modelfc.corner_api import create_app
from modelfc.ledger_storage import LedgerError, LedgerStorageUnavailable
from modelfc.providers.oddspapi import decimal_to_american

NOW = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)


def fixture(**changes):
    return BttsFixture(**(dict(competition="E1", provider="test-provider", provider_fixture_id="fixture-1",
        home_team="A", away_team="D", kickoff_utc="2026-12-31T15:00:00Z") | changes))


def history():
    rows = ["Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,HC,AC"]
    pairs = (("A", "B", 2, 1), ("C", "D", 1, 3), ("B", "A", 0, 2), ("D", "C", 4, 0))
    for index in range(100):
        home, away, hg, ag = pairs[index % 4]
        day = date(2026, 7, 2) + timedelta(days=index // 4)
        rows.append(f"E1,{day:%d/%m/%Y},{home},{away},{hg},{ag},5,4")
    return history_from_bytes("E1", (("E1_2627.csv", ("\n".join(rows) + "\n").encode()),))


def forecast():
    return freeze_btts_forecast(fixture(), history(), frozen_at=NOW - timedelta(minutes=2))


def observation(*, retrieved=NOW, prices=None, statuses=None, snapshot="a"):
    prices = prices if prices is not None else {"draftkings": (2.2, 1.75), "fanduel": (2.05, 1.9)}
    statuses = statuses or {book: "AVAILABLE" if book in prices else "UNKNOWN" for book in ("draftkings", "fanduel")}
    selections = []
    for book, pair in prices.items():
        for side, price in zip(("YES", "NO"), pair):
            if price is not None:
                selections.append(BttsSelection(competition="E1", provider="test-provider", provider_fixture_id="fixture-1",
                    bookmaker=book, side=side, american_odds=decimal_to_american(price), decimal_odds=price,
                    retrieved_at_utc=retrieved.isoformat(), provider_quote_reference=digest([book, side, price, snapshot])))
    return BttsObservation(competition="E1", fixture=fixture(), retrieved_at_utc=retrieved.isoformat(),
        selections=tuple(selections), availability=tuple(BookAvailability(competition="E1", bookmaker=b, status=s)
            for b, s in statuses.items()), provider_snapshot_sha256=digest(snapshot), provider_metadata_sha256=digest("metadata"))


class BttsModelTests(unittest.TestCase):
    def test_frozen_arithmetic_rates_match_deepfc_method(self):
        result = forecast()
        # League means 1.75/1.5, 25 venue matches for each team, prior N=5.
        self.assertAlmostEqual(result.home_expected_goals, 37 / 24)
        self.assertAlmostEqual(result.away_expected_goals, 23 / 12)
        self.assertEqual(result.inputs.history_matches, 100)
        self.assertEqual(result.competition, "E1")
        self.assertEqual(result.deepfc_source_commit, DEEPFC_SOURCE_COMMIT)
        self.assertEqual(result.smoothing_matches, 5)

    def test_poisson_and_complement(self):
        import math
        yes, no = btts_probabilities(1.4, 0.9)
        self.assertAlmostEqual(yes, (1 - math.exp(-1.4)) * (1 - math.exp(-0.9)))
        self.assertEqual(yes + no, 1)
        self.assertEqual(btts_probabilities(0, 1), (0, 1))
        self.assertEqual(btts_probabilities(1000, 1000), (1, 0))
        for value in (-1, float("nan"), float("inf"), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                btts_probabilities(value, 1)

    def test_no_future_or_same_date_leakage(self):
        base = history()
        added = tuple(GoalResult(competition="E1", match_date=NOW.date() + timedelta(days=d),
            home_team="A", away_team="D", home_goals=20, away_goals=20) for d in (0, 1))
        future = GoalHistory(competition="E1", sources=base.sources, results=base.results + added)
        self.assertEqual(freeze_btts_forecast(fixture(), future, frozen_at=NOW),
                         freeze_btts_forecast(fixture(), base, frozen_at=NOW))
        with self.assertRaises(ValueError):
            freeze_btts_forecast(fixture(), base, frozen_at=datetime(2027, 1, 1, tzinfo=timezone.utc))

    def test_minimum_history_is_frozen_and_new_teams_use_prior(self):
        base = history()
        small = GoalHistory(competition="E1", sources=base.sources, results=base.results[:99])
        with self.assertRaisesRegex(ValueError, "INSUFFICIENT"):
            freeze_btts_forecast(fixture(), small, frozen_at=NOW)
        new = fixture().model_copy(update={"home_team": "New home", "away_team": "New away"})
        result = freeze_btts_forecast(new, base, frozen_at=NOW)
        self.assertEqual((result.home_expected_goals, result.away_expected_goals), (1.75, 1.5))

    def test_competition_gate_and_mixed_history_rejected(self):
        for competition in ("E0", "SP1", "I1", "D1", "../E1"):
            with self.subTest(competition=competition), self.assertRaises(ValueError):
                history_from_bytes(competition, ())
        with self.assertRaises(ValidationError):
            BttsFixture(**{**fixture().model_dump(), "competition": "E0"})
        with self.assertRaises(ValidationError):
            GoalHistory(competition="E1", sources=history().sources,
                        results=history().results + (history().results[0],))

    def test_csv_completion_and_invalid_contracts(self):
        header = b"Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,HC,AC\n"
        value = header + b"E1,01/07/26,A,B,2,1,5,4\nE1,02/07/26,C,D,0,1,,\nE1,03/07/26,C,D,,,,\n"
        self.assertEqual(len(history_from_bytes("E1", (("E1_2627.csv", value),)).results), 1)
        for row in (b"E0,01/07/26,A,B,2,1,5,4", b"E1,01/07/26,A,B,2,,5,4",
                    b"E1,01/07/26,A,B,2,1,5,", b"E1,01/07/26,A,B,-1,1,5,4",
                    b"E1,31/02/26,A,B,2,1,5,4", b"E1,01/07/26,A,A,2,1,5,4"):
            with self.subTest(row=row), self.assertRaises(ValueError):
                history_from_bytes("E1", (("E1_2627.csv", header + row + b"\n"),))

    def test_configured_loader_filters_competition_and_never_creates_lock(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            config.write_text(json.dumps({"data_directory": ".", "leagues": ["E1", "E0"], "max_age_days": 14}))
            source = b"Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,HC,AC\nE1,01/07/26,A,B,2,1,5,4\n"
            (root / "E1_2627.csv").write_bytes(source)
            (root / "E0_2627.csv").write_text("invalid other league must not be read")
            with self.assertRaises(LedgerStorageUnavailable):
                load_goal_history(config, "E1")
            self.assertFalse((root / "data").exists())
            lock = root / "data/corner-refresh/refresh.lock"
            lock.parent.mkdir(parents=True)
            lock.touch()
            loaded = load_goal_history(config, "E1")
            self.assertEqual([s.filename for s in loaded.sources], ["E1_2627.csv"])
            (root / "E1_2627.csv").unlink()
            (root / "E1_2627.csv").symlink_to(root / "E0_2627.csv")
            with self.assertRaises(OSError):
                load_goal_history(config, "E1")

    def test_frozen_model_and_provenance_validation(self):
        original = forecast()
        for field, value in (("model_version", "new"), ("deepfc_source_commit", "0" * 40),
                             ("home_expected_goals", 10), ("yes_probability", 0.01)):
            changed = original.model_dump(mode="json")
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError):
                type(original).model_validate_json(json.dumps(changed))
        with self.assertRaises(ValidationError):
            original.yes_probability = 0.2


class BttsMarketTests(unittest.TestCase):
    def test_paired_no_vig_and_actual_decimal_ev_for_both_sides(self):
        obs = observation()
        yes, no = paired_values(obs.selections[0], obs.selections[1], 0.6)
        total = 1 / 2.2 + 1 / 1.75
        self.assertAlmostEqual(yes.no_vig_market_probability, (1 / 2.2) / total)
        self.assertAlmostEqual(no.no_vig_market_probability, (1 / 1.75) / total)
        self.assertAlmostEqual(yes.expected_profit, 0.6 * 1.2 - 0.4)
        self.assertAlmostEqual(no.expected_profit, 0.4 * 0.75 - 0.6)
        self.assertAlmostEqual(yes.model_minus_market_difference + no.model_minus_market_difference, 0)

    def test_positive_negative_zero_differences_are_research_not_thresholds(self):
        obs = observation(prices={"draftkings": (2.0, 2.0)})
        for probability in (0.4, 0.5, 0.6):
            yes, no = paired_values(*obs.selections, probability)
            self.assertAlmostEqual(yes.model_minus_market_difference, probability - 0.5)
            self.assertAlmostEqual(no.model_minus_market_difference, 0.5 - probability)
        records = comparisons_from_snapshot(forecast(), obs)
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0].research_only)
        self.assertNotIn("qualified", records[0].model_dump())

    def test_never_pair_across_books_or_observations(self):
        obs = observation()
        with self.assertRaises(ValueError):
            paired_values(obs.selections[0], obs.selections[3], 0.6)
        later = observation(retrieved=NOW + timedelta(seconds=1))
        with self.assertRaises(ValueError):
            paired_values(obs.selections[0], later.selections[1], 0.6)
        incomplete = observation(prices={"draftkings": (2.0, None), "fanduel": (None, 2.0)},
                                 statuses={"draftkings": "UNKNOWN", "fanduel": "UNKNOWN"})
        self.assertEqual(comparisons_from_snapshot(forecast(), incomplete), ())

    def test_malformed_pairs_and_price_consistency(self):
        obs = observation()
        value = obs.model_dump(mode="json")
        value["selections"].append(value["selections"][0])
        with self.assertRaises(ValidationError):
            BttsObservation.model_validate_json(json.dumps(value))
        for field, bad in (("decimal_odds", 10), ("american_odds", 50), ("competition", "SP1"),
                           ("retrieved_at_utc", "2026-09-21T12:00:00")):
            selection = obs.selections[0].model_dump(mode="json")
            selection[field] = bad
            with self.subTest(field=field), self.assertRaises(ValueError):
                BttsSelection.model_validate_json(json.dumps(selection))

    def test_best_yes_and_no_are_independent_and_keep_own_no_vig(self):
        record = make_record(forecast(), observation())
        views = research_views((record,), "E1", as_of=NOW)
        yes = next(v for v in views if v.best_yes_price)
        no = next(v for v in views if v.best_no_price)
        self.assertEqual(yes.bookmaker, "draftkings")
        self.assertEqual(no.bookmaker, "fanduel")
        self.assertAlmostEqual(yes.yes.no_vig_market_probability, (1 / 2.2) / (1 / 2.2 + 1 / 1.75))
        self.assertAlmostEqual(no.no.no_vig_market_probability, (1 / 1.9) / (1 / 2.05 + 1 / 1.9))

    def test_exact_ties_deterministic(self):
        record = make_record(forecast(), observation(prices={"draftkings": (2.0, 2.0), "fanduel": (2.0, 2.0)}))
        for values in ((record,), tuple(reversed((record,)))):
            views = research_views(values, "E1", as_of=NOW)
            self.assertEqual([v.bookmaker for v in views if v.best_yes_price], ["draftkings"])
            self.assertEqual([v.bookmaker for v in views if v.best_no_price], ["draftkings"])

    def test_stale_future_and_post_kickoff_are_historical_only(self):
        record = make_record(forecast(), observation())
        for at, status in ((NOW + timedelta(seconds=301), "STALE"),
                           (NOW - timedelta(seconds=1), "FUTURE_OBSERVATION"),
                           (datetime(2027, 1, 1, tzinfo=timezone.utc), "KICKED_OFF")):
            views = research_views((record,), "E1", as_of=at)
            self.assertEqual({v.current_status for v in views}, {status})
            self.assertFalse(any(v.best_yes_price or v.best_no_price for v in views))
        self.assertTrue(any(v.best_yes_price for v in research_views((record,), "E1", as_of=NOW + timedelta(seconds=301), max_age_seconds=302)))

    def test_later_unavailable_and_unknown_invalidate_best_without_deleting_quotes(self):
        frozen = forecast()
        old = make_record(frozen, observation())
        for status in ("UNAVAILABLE", "UNKNOWN"):
            new = make_record(frozen, observation(retrieved=NOW + timedelta(seconds=1), prices={},
                statuses={"draftkings": status, "fanduel": status}, snapshot="later"))
            views = research_views((old, new), "E1", as_of=NOW + timedelta(seconds=2))
            self.assertEqual(len(views), 2)
            self.assertEqual({v.current_status for v in views}, {status})
            self.assertFalse(any(v.best_yes_price or v.best_no_price for v in views))
        self.assertEqual(old, make_record(frozen, observation()))


class BttsEvidenceApiTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name) / "state"
        self.client = TestClient(create_app(state_dir=self.state))
        self.frozen = forecast()

    def test_empty_get_no_state_no_writes_and_unsupported_competitions(self):
        response = self.client.get("/api/v1/research/btts?competition=E1")
        self.assertEqual((response.status_code, response.json()), (200, []))
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertFalse(self.state.exists())
        for competition in ("E0", "SP1", "I1", "D1", "../E1"):
            response = self.client.get("/api/v1/research/btts", params={"competition": competition})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["error"]["code"], "UNSUPPORTED_COMPETITION")
        self.assertEqual(self.client.post("/api/v1/research/btts").status_code, 405)

    def test_populated_api_no_writes_no_corner_recommendations_and_deterministic_order(self):
        record = record_btts_research(self.state, self.frozen, observation())
        paths = list(self.state.rglob("*"))
        before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in paths if p.is_file()}
        response = self.client.get("/api/v1/research/btts?competition=E1")
        self.assertEqual(response.status_code, 200, response.text)
        values = response.json()
        self.assertEqual(len(values), 2)
        self.assertEqual([v["bookmaker"] for v in values], ["draftkings", "fanduel"])
        self.assertTrue(all(v["competition"] == "E1" and v["research_only"] for v in values))
        self.assertEqual(values[0]["deepfc_source_commit"], DEEPFC_SOURCE_COMMIT)
        self.assertEqual(values[0]["fixture"]["provider_fixture_id"], "fixture-1")
        self.assertNotIn("record_hash", response.text)
        self.assertNotIn(str(self.state), response.text)
        replay = self.client.get("/api/v1/research/btts").json()
        self.assertEqual([v["comparison_id"] for v in values], [v["comparison_id"] for v in replay])
        after = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in paths if p.is_file()}
        self.assertEqual(before, after)
        for route in ("recommendations", "predictions", "opportunities"):
            self.assertEqual(self.client.get(f"/api/v1/{route}").json(), [])
        self.assertEqual(read_btts_research(self.state, "E1", as_of=NOW)[0].observation_id, digest(record.observation))

    def test_api_current_flags_and_replay_use_one_fixed_clock(self):
        record_btts_research(self.state, self.frozen, observation())
        with patch("modelfc.btts_research.datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            first = self.client.get("/api/v1/research/btts").json()
            second = self.client.get("/api/v1/research/btts?competition=E1").json()
        self.assertEqual(first, second)
        self.assertEqual([v["current_status"] for v in first], ["AVAILABLE", "AVAILABLE"])
        self.assertTrue(first[0]["best_yes_price"])
        self.assertTrue(first[1]["best_no_price"])

    def test_history_duplicate_excluded_rows_and_missing_completed_goals_rejected(self):
        header = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,HC,AC\n"
        for rows in ("E1,01/09/2026,A,B,1,0,,\n" * 2,
                     "E1,01/09/2026,A,B,,,3,4\n"):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                history_from_bytes("E1", (("E1_2627.csv", (header + rows).encode()),))

    def test_zero_venue_count_cannot_preserve_nonzero_goals(self):
        raw = self.frozen.model_dump(mode="json")
        raw["inputs"]["home_venue_matches"] = 0
        with self.assertRaises(ValidationError):
            type(self.frozen).model_validate_json(json.dumps(raw))

    def test_writer_rejects_symlink_lock_without_touching_target(self):
        directory = self.state / "btts-research"
        directory.mkdir(parents=True)
        target = self.state / "private"
        target.write_text("untouched")
        (directory / ".lock").symlink_to(target)
        with self.assertRaises(LedgerStorageUnavailable):
            record_btts_research(self.state, self.frozen, observation())
        self.assertEqual(target.read_text(), "untouched")

    def test_existing_provider_fixture_identity_cannot_be_rewritten(self):
        record_btts_research(self.state, self.frozen, observation())
        changed_fixture = fixture(kickoff_utc="2026-12-30T15:00:00Z")
        changed_forecast = freeze_btts_forecast(changed_fixture, history(), frozen_at=NOW - timedelta(minutes=2))
        changed_observation = observation().model_copy(update={"fixture": changed_fixture})
        with self.assertRaises(LedgerError):
            record_btts_research(self.state, changed_forecast, changed_observation)

    def test_future_competition_configuration_reuses_model_and_api_filter(self):
        # Simulate a future explicit registry enablement without enabling E0 in V1.
        enabled = frozenset({"E1", "E0"})
        with patch("modelfc.btts_market_data.ENABLED_COMPETITIONS", enabled), \
                patch("modelfc.corner_api.ENABLED_COMPETITIONS", enabled):
            foreign_fixture = fixture(competition="E0", provider_fixture_id="e0-fixture")
            foreign_history = GoalHistory.model_validate_json(history().model_dump_json().replace("E1", "E0"))
            foreign_forecast = freeze_btts_forecast(foreign_fixture, foreign_history,
                                                    frozen_at=NOW - timedelta(minutes=2))
            self.assertEqual(foreign_forecast.home_expected_goals, self.frozen.home_expected_goals)
            raw = observation().model_dump(mode="json")
            raw["fixture"] = foreign_fixture.model_dump(mode="json")
            raw["competition"] = "E0"
            for value in (*raw["selections"], *raw["availability"]):
                value["competition"] = "E0"
                if "provider_fixture_id" in value:
                    value["provider_fixture_id"] = "e0-fixture"
            foreign_observation = BttsObservation.model_validate_json(json.dumps(raw))
            record_btts_research(self.state, self.frozen, observation())
            record_btts_research(self.state, foreign_forecast, foreign_observation)
            for competition in ("E1", "E0"):
                response = self.client.get("/api/v1/research/btts", params={"competition": competition})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(len(response.json()), 2)
                self.assertEqual({v["competition"] for v in response.json()}, {competition})
        self.assertEqual(self.client.get("/api/v1/research/btts?competition=E0").status_code, 422)

    def test_append_only_idempotent_and_freeze_cannot_change(self):
        first = record_btts_research(self.state, self.frozen, observation())
        path = self.state / "btts-research" / f"{first.record_id}.json"
        original = (path.stat().st_ino, path.read_bytes())
        self.assertEqual(record_btts_research(self.state, self.frozen, observation()), first)
        record_btts_research(self.state, self.frozen, observation(retrieved=NOW + timedelta(seconds=1), snapshot="new"))
        self.assertEqual((path.stat().st_ino, path.read_bytes()), original)
        self.assertEqual(len(list(path.parent.glob("*.json"))), 2)
        changed = freeze_btts_forecast(fixture(), history(), frozen_at=NOW - timedelta(minutes=1))
        with self.assertRaises(LedgerError):
            record_btts_research(self.state, changed, observation())

    def test_corrupt_math_hash_and_model_provenance_fail_closed(self):
        original = make_record(self.frozen, observation()).model_dump(mode="json")
        for kind in ("hash", "math", "source", "rate"):
            value = json.loads(json.dumps(original))
            if kind == "hash":
                value["record_hash"] = "0" * 64
            elif kind == "math":
                value["comparisons"][0]["yes"]["expected_profit"] = 99
            elif kind == "source":
                value["forecast"]["deepfc_source_commit"] = "0" * 40
            else:
                value["forecast"]["home_expected_goals"] = 20
            if kind != "hash":
                value["record_hash"] = digest({k: v for k, v in value.items() if k != "record_hash"})
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                ResearchRecord.model_validate_json(json.dumps(value))

    def test_integrity_and_storage_errors_sanitized(self):
        record = record_btts_research(self.state, self.frozen, observation())
        path = self.state / "btts-research" / f"{record.record_id}.json"
        path.write_text('{"secret_path":"/private/ledger"}')
        response = self.client.get("/api/v1/research/btts")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "LEDGER_INTEGRITY_FAILURE")
        self.assertNotIn("/private", response.text)
        with patch("modelfc.btts_research.ledger_read_lock", side_effect=LedgerStorageUnavailable("/private/secret")):
            response = self.client.get("/api/v1/research/btts")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("/private", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_symlink_and_missing_lock_fail_closed(self):
        self.state.mkdir()
        directory = self.state / "btts-research"
        directory.mkdir()
        self.assertEqual(self.client.get("/api/v1/research/btts").status_code, 503)
        self.assertFalse((directory / ".lock").exists())
        (directory / ".lock").touch()
        secret = self.state.parent / "secret.json"
        secret.write_text("secret data")
        (directory / f"{'a' * 64}.json").symlink_to(secret)
        response = self.client.get("/api/v1/research/btts")
        self.assertGreaterEqual(response.status_code, 400)
        self.assertNotIn("secret data", response.text)


if __name__ == "__main__":
    unittest.main()
