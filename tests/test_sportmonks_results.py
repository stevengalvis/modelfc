from datetime import datetime, timezone
import json
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError

from modelfc.providers.sportmonks_results import (SportmonksRecentResultsClient,
    SportmonksRejected, normalize_page)


NOW = datetime(2026, 10, 11, 1, tzinfo=timezone.utc)
BINDINGS = ([{"provider_team_id": 101, "provider_name": "Home Source", "history_name": "Home"},
             {"provider_team_id": 102, "provider_name": "Away Source", "history_name": "Away"}]
            + [{"provider_team_id": 1000+i, "provider_name": f"Source {i}",
                "history_name": f"Team {i}"} for i in range(22)])


def fixture():
    return {"id": 19729059, "league_id": 9, "season_id": 27903,
        "starting_at": "2026-10-09T19:00:00Z", "starting_at_timestamp": 1791572400,
        "state": {"short_name": "FT"},
        "participants": [
            {"id": 102, "name": "Away Source", "meta": {"location": "away"}},
            {"id": 101, "name": "Home Source", "meta": {"location": "home"}}],
        "scores": [
            {"description": "CURRENT", "participant_id": 101, "score": {"goals": 1}},
            {"description": "CURRENT", "participant_id": 102, "score": {"goals": 1}}],
        "statistics": [
            {"type_id": 34, "participant_id": 101, "location": "home", "data": {"value": 4}},
            {"type_id": 34, "participant_id": 102, "location": "away", "data": {"value": 1}}]}


def raw(rows=None, has_more=False, next_page=None):
    return json.dumps({"data": [fixture()] if rows is None else rows,
        "pagination": {"has_more": has_more, "next_page": next_page}}).encode()


class Response:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def getcode(self): return 200
    def read(self, limit): return self.body


class SportmonksResultTests(unittest.TestCase):
    def test_exact_completed_pair_and_provenance(self):
        record = normalize_page(raw(), retrieved_at=NOW, bindings=BINDINGS,
                                expected_season_id=27903)[0]
        self.assertEqual((record.home_team, record.away_team, record.home_goals,
                          record.away_goals, record.home_corners, record.away_corners),
                         ("Home", "Away", 1, 1, 4, 1))
        self.assertEqual(record.response_sha256, __import__("hashlib").sha256(raw()).hexdigest())

    def test_null_fractional_duplicate_ambiguous_and_incomplete_corners_reject(self):
        mutations = []
        for value in (None, -1, 1.5):
            row = fixture(); row["statistics"][0]["data"]["value"] = value; mutations.append(row)
        row = fixture(); row["statistics"].append(dict(row["statistics"][0])); mutations.append(row)
        row = fixture(); row["statistics"] = row["statistics"][:1]; mutations.append(row)
        row = fixture(); row["statistics"][0]["location"] = "away"; mutations.append(row)
        for row in mutations:
            with self.subTest(row=row), self.assertRaises(SportmonksRejected):
                normalize_page(raw([row]), retrieved_at=NOW, bindings=BINDINGS,
                               expected_season_id=27903)

    def test_identity_score_status_and_competition_fail_closed(self):
        mutations = []
        row=fixture(); row["participants"][0]["name"]="Guess"; mutations.append(row)
        row=fixture(); row["scores"].append(dict(row["scores"][0])); mutations.append(row)
        row=fixture(); row["league_id"]=325; mutations.append(row)
        row=fixture(); row["season_id"]=1; mutations.append(row)
        for row in mutations:
            with self.subTest(row=row), self.assertRaises(SportmonksRejected):
                normalize_page(raw([row]), retrieved_at=NOW, bindings=BINDINGS,
                               expected_season_id=27903)
        row=fixture(); row["state"]={"short_name":"NS"}
        self.assertEqual(normalize_page(raw([row]), retrieved_at=NOW, bindings=BINDINGS,
                                       expected_season_id=27903), ())

    def test_pagination_is_bounded_no_retry_and_cursor_url_is_not_followed(self):
        next_url = "https://api.sportmonks.com/v3/football/fixtures/between/2026-10-09/2026-10-10?cursor=abc"
        first = raw([], True, next_url); second = raw([], False, None)
        opener = Mock(); opener.open.side_effect = [Response(first), Response(second)]
        result = SportmonksRecentResultsClient("secret", opener=opener, clock=lambda:NOW).fetch(
            __import__("datetime").date(2026,10,9), __import__("datetime").date(2026,10,10),
            bindings=BINDINGS, season_id=27903)
        self.assertEqual(result.requests, 2)
        self.assertEqual(opener.open.call_count, 2)
        requests = [call.args[0] for call in opener.open.call_args_list]
        self.assertTrue(all(request.method == "GET" and "secret" not in request.full_url
                            for request in requests))
        self.assertIn("cursor=abc", requests[1].full_url)

    def test_http_failure_is_sanitized_and_not_retried(self):
        opener=Mock(); opener.open.side_effect=HTTPError("https://secret.invalid",400,"bad",{},None)
        with self.assertRaisesRegex(SportmonksRejected,"SPORTMONKS_HTTP_400") as caught:
            SportmonksRecentResultsClient("private-token",opener=opener,clock=lambda:NOW).fetch(
                __import__("datetime").date(2026,10,9), __import__("datetime").date(2026,10,10),
                bindings=BINDINGS,season_id=27903)
        self.assertEqual(opener.open.call_count,1)
        self.assertNotIn("private-token",str(caught.exception))


if __name__ == "__main__": unittest.main()
