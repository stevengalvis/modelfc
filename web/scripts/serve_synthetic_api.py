"""Run the real backend for frontend HTTP integration, using backend-owned synthetic data.

Use the merged backend from this repository (src and repository on PYTHONPATH).
No network data, real ledger records, or backend implementation are modified.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os

import uvicorn
from modelfc.corner_api import create_app
from tests.generate_api_response_fixtures import HISTORY

with TemporaryDirectory(prefix="modelfc-frontend-integration-") as directory:
    root = Path(directory)
    completed = HISTORY.replace("Div,Date,HomeTeam,AwayTeam,HC,AC", "Div,Date,HomeTeam,AwayTeam,HC,AC,FTHG,FTAG,FTR")
    completed = "\n".join(line if index == 0 else line + ",0,0,D" for index, line in enumerate(completed.splitlines())) + "\n"
    (root / "E1_2627.csv").write_text(completed, encoding="utf-8")
    (root / "data/corner-refresh").mkdir(parents=True)
    (root / "data/corner-refresh/refresh.lock").touch()
    config = root / "corner_data.json"
    config.write_text(json.dumps({
        "data_directory": ".", "leagues": ["E1"], "max_age_days": 14,
    }), encoding="utf-8")
    app = create_app(
        data_config_path=config, state_dir=root / "state",
        min_history=4, min_venue_history=2,
        cors_origins=["http://127.0.0.1:3000", "http://127.0.0.1:3001"],
    )
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("MODELFC_TEST_API_PORT", "8000")))
