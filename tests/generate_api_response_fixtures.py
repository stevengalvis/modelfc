"""Generate deterministic V1 HTTP response examples through FastAPI."""

from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient

from modelfc.corner_api import create_app


HISTORY = (
    "Div,Date,HomeTeam,AwayTeam,HC,AC\n"
    "E1,01/09/2026,Birmingham,Millwall,4,3\n"
    "E1,03/09/2026,Millwall,Birmingham,7,2\n"
    "E1,05/09/2026,Birmingham,Millwall,5,5\n"
    "E1,07/09/2026,Millwall,Birmingham,2,3\n"
    "E1,10/09/2026,Birmingham,Millwall,8,4\n"
    "E1,14/09/2026,Millwall,Birmingham,6,1\n"
)


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _normalize_temporary_path(value: object, root: Path) -> object:
    """Keep generated error fixtures stable across temporary directories."""
    encoded = json.dumps(value).replace(str(root), "<TEMP_DATA_ROOT>")
    return json.loads(encoded)


def generate(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "E1_2627.csv").write_text(HISTORY, encoding="utf-8")
        config = root / "corner_data.json"
        config.write_text(json.dumps({
            "data_directory": ".", "leagues": ["E1"], "max_age_days": 14,
        }), encoding="utf-8")
        refresh = root / "data" / "corner-refresh"
        refresh.mkdir(parents=True)
        (refresh / "status.json").write_text(json.dumps({
            "checked_at": "2026-09-16T03:17:39+00:00",
            "season": "2627", "max_age_days": 14,
            "results": [{
                "league": "E1", "status": "unchanged", "matches": 6,
                "added": 0, "corrected": 0, "latest_match": "2026-09-14",
                "stale": False,
            }],
        }), encoding="utf-8")
        client = TestClient(create_app(
            data_config_path=config, state_dir=root / "state",
            min_history=4, min_venue_history=2,
        ))

        with patch("modelfc.corner_capabilities.datetime") as clock:
            clock.now.return_value = datetime(2026, 9, 17, tzinfo=timezone.utc)
            _write(destination / "capabilities.json", client.get(
                "/api/v1/capabilities",
            ).json())

        base = {
            "fixture": {
                "competition": "E1", "date": "2026-10-01",
                "home_team": "Birmingham", "away_team": "Millwall",
            },
            "model": "venue-opponent-negative-binomial",
            "markets": [
                {
                    "client_market_id": "home-o4.5", "market_type": "TEAM_TOTAL",
                    "team_side": "HOME", "side": "OVER", "line": 4.5,
                    "american_odds": -110,
                },
                {
                    "client_market_id": "match-o9.5", "market_type": "MATCH_TOTAL",
                    "team_side": None, "side": "OVER", "line": 9.5,
                    "american_odds": 105,
                },
            ],
        }
        deterministic = (
            patch("modelfc.corner_analysis_store.uuid.uuid4", return_value=uuid.UUID(
                "11111111-1111-4111-8111-111111111111"
            )),
            patch(
                "modelfc.corner_analysis_store.utc_timestamp",
                return_value="2026-09-17T12:00:00+00:00",
            ),
            patch(
                "modelfc.corner_analysis_store.git_commit_sha",
                return_value="fixture-model-version",
            ),
        )
        with deterministic[0], deterministic[1], deterministic[2]:
            analysis = client.post("/api/v1/analyses", json={
                **base, "idempotency_key": "fixture-stale-analysis",
            })
            _write(destination / "analysis.json", analysis.json())

            whole_line = client.post("/api/v1/analyses", json={
                **base,
                "idempotency_key": "fixture-whole-line",
                "fixture": {**base["fixture"], "date": "2026-09-17"},
                "markets": [{
                    "client_market_id": "home-o4", "market_type": "TEAM_TOTAL",
                    "team_side": "HOME", "side": "OVER", "line": 4,
                    "american_odds": -110,
                }],
            })
            _write(destination / "analysis_whole_line.json", whole_line.json())

        unavailable_config = root / "unavailable.json"
        unavailable_config.write_text(json.dumps({
            "data_directory": "missing", "leagues": ["E1"], "max_age_days": 14,
        }), encoding="utf-8")
        unavailable = TestClient(create_app(
            data_config_path=unavailable_config,
            state_dir=root / "unavailable-state",
            min_history=4, min_venue_history=2,
        )).post("/api/v1/analyses", json={
            **base, "idempotency_key": "fixture-unavailable-data",
        })
        _write(
            destination / "analysis_unavailable_data.json",
            _normalize_temporary_path(unavailable.json(), root),
        )


if __name__ == "__main__":
    generate(Path(__file__).parent / "fixtures" / "api_v1")
