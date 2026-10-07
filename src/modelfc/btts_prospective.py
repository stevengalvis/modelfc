"""Research-only consumer of supplied prospective acquisitions. No provider I/O."""

import csv

from modelfc.btts_market_data import BttsFixture, require_competition, utc
from modelfc.btts_model import freeze_btts_forecast, load_goal_history
from modelfc.btts_research import get_or_freeze_btts_forecast, record_btts_research
from modelfc.ledger_storage import LedgerError, LedgerStorageUnavailable

COUNTERS = ("forecasts_frozen", "snapshots_recorded", "comparisons_recorded", "unavailable_incomplete")
REASONS = frozenset({"HISTORY_UNAVAILABLE", "HISTORY_INSUFFICIENT", "FORECAST_REVIEW",
                     "SNAPSHOT_REVIEW", "STORAGE_OR_INTEGRITY_FAILURE"})


def new_summary():
    return {"status": "NOT_APPLICABLE", **dict.fromkeys(COUNTERS, 0), "reasons": []}


def validate_summary(summary):
    if (not isinstance(summary, dict) or set(summary) != {"status", "reasons", *COUNTERS}
            or summary["status"] not in {"OK", "REVIEW", "NOT_APPLICABLE"}
            or not isinstance(summary["reasons"], list)
            or any(not isinstance(r, str) or r not in REASONS for r in summary["reasons"])
            or len(summary["reasons"]) != len(set(summary["reasons"]))
            or (summary["status"] == "REVIEW") != bool(summary["reasons"])
            or any(type(summary[c]) is not int or summary[c] < 0 for c in COUNTERS)
            or summary["comparisons_recorded"] > 2 * summary["snapshots_recorded"]
            or summary["unavailable_incomplete"] > 2 * summary["snapshots_recorded"]
            or summary["status"] == "NOT_APPLICABLE" and any(summary[c] for c in COUNTERS)):
        raise ValueError("INVALID_BTTS_RESEARCH_SUMMARY")
    return summary


class BttsResearchAcquisition:
    def __init__(self, state, config, summary, clock):
        self.state, self.config, self.summary, self.clock = state, config, summary, clock
        self.history = None

    def _review(self, reason):
        self.summary["status"] = "REVIEW"
        if reason not in self.summary["reasons"]:
            self.summary["reasons"].append(reason)

    def prepare(self, fixture, *, allow_new_forecast=True):
        """Freeze and durably publish before the caller makes its odds request."""
        if self.summary["status"] == "NOT_APPLICABLE":
            self.summary["status"] = "OK"
        try:
            require_competition(fixture.competition)
            identity = BttsFixture(competition=fixture.competition, provider=fixture.provider,
                provider_fixture_id=fixture.provider_fixture_id, home_team=fixture.home_team,
                away_team=fixture.away_team, kickoff_utc=utc(fixture.kickoff_utc))
            def freeze():
                if not allow_new_forecast:
                    raise ValueError("NO_PROSPECTIVE_BTTS_FORECAST")
                if self.history is None:
                    self.history = load_goal_history(self.config, fixture.competition)
                names = {name for result in self.history.results for name in (result.home_team, result.away_team)}
                if not {identity.home_team, identity.away_team} <= names:
                    raise ValueError("BTTS_FIXTURE_IDENTITY_REVIEW")
                return freeze_btts_forecast(identity, self.history, frozen_at=self.clock())
            forecast, created = get_or_freeze_btts_forecast(self.state, identity, freeze)
            self.summary["forecasts_frozen"] += int(created)
            return forecast
        except LedgerStorageUnavailable:
            # Covers inaccessible history lock as well as research storage. Fixed code only.
            self._review("HISTORY_UNAVAILABLE")
        except LedgerError:
            self._review("STORAGE_OR_INTEGRITY_FAILURE")
        except OSError:
            self._review("HISTORY_UNAVAILABLE")
        except csv.Error:
            self._review("FORECAST_REVIEW")
        except ValueError as error:
            self._review("HISTORY_INSUFFICIENT" if str(error) == "INSUFFICIENT_BTTS_HISTORY" else "FORECAST_REVIEW")
        return None

    def observe(self, client, supplied_corner_observation, forecast):
        if forecast is None:
            return
        try:
            observation = client.normalize_btts_snapshot(supplied_corner_observation,
                historical_names={forecast.fixture.home_team, forecast.fixture.away_team}, as_of=self.clock())
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            self._review("SNAPSHOT_REVIEW")
            return
        try:
            record = record_btts_research(self.state, forecast, observation)
            self.summary["snapshots_recorded"] += 1
            self.summary["comparisons_recorded"] += len(record.comparisons)
            self.summary["unavailable_incomplete"] += sum(s.status != "AVAILABLE" for s in observation.availability)
        except (LedgerError, OSError):
            self._review("STORAGE_OR_INTEGRITY_FAILURE")
        except (ValueError, TypeError, KeyError, AttributeError):
            self._review("SNAPSHOT_REVIEW")
