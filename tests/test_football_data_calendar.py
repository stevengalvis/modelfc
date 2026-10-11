from datetime import datetime, timezone
import json
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError

from modelfc.history_completeness import parse_calendar
from modelfc.providers.football_data_calendar import (
    CalendarFetchRejected, FootballDataCalendarClient, MAX_CALENDAR_BYTES,
)
from tests.test_history_completeness import bundle as fixture_bundle


NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


class Response:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def getcode(self): return 200
    def read(self, limit): return self.body


class FootballDataCalendarClientTests(unittest.TestCase):
    def test_one_bounded_get_builds_valid_fresh_bundle(self):
        value = fixture_bundle()
        payload = value["payload"].encode()
        opener = Mock(); opener.open.return_value = Response(payload)
        fetched = FootballDataCalendarClient("private-token", opener=opener,
            clock=lambda: NOW).fetch(2026, team_bindings=value["team_bindings"])
        self.assertEqual(fetched.requests, 1)
        calendar = parse_calendar(fetched.raw, as_of=NOW)
        self.assertEqual(len(calendar.matches), 552)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.full_url,
                         "https://api.football-data.org/v4/competitions/ELC/matches?season=2026")
        self.assertNotIn("private-token", request.full_url)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 30)

    def test_http_and_size_failures_are_sanitized_and_not_retried(self):
        value = fixture_bundle()
        for failure, code in ((HTTPError("https://private.invalid", 403, "secret", {}, None),
                               "CALENDAR_HTTP_403"),
                              (Response(b"x" * (MAX_CALENDAR_BYTES + 1)),
                               "CALENDAR_RESPONSE_SIZE_REJECTED")):
            opener = Mock()
            if isinstance(failure, Response): opener.open.return_value = failure
            else: opener.open.side_effect = failure
            with self.subTest(code=code), self.assertRaisesRegex(CalendarFetchRejected, code) as caught:
                FootballDataCalendarClient("private-token", opener=opener,
                    clock=lambda: NOW).fetch(2026, team_bindings=value["team_bindings"])
            self.assertEqual(opener.open.call_count, 1)
            self.assertNotIn("private-token", str(caught.exception))

    def test_malformed_or_incomplete_calendar_fails_closed(self):
        value = fixture_bundle()
        payload = json.loads(value["payload"]); payload["matches"].pop()
        payload["resultSet"]["count"] -= 1
        opener = Mock(); opener.open.return_value = Response(json.dumps(payload).encode())
        with self.assertRaisesRegex(CalendarFetchRejected, "CALENDAR_RESPONSE_REJECTED"):
            FootballDataCalendarClient("private-token", opener=opener,
                clock=lambda: NOW).fetch(2026, team_bindings=value["team_bindings"])


if __name__ == "__main__": unittest.main()
