"""Generate a synthetic public projection for frontend contract tests."""
import json
from pathlib import Path
import tempfile
from datetime import timedelta
from modelfc.btts_research import record_btts_research
from modelfc.match_evidence import match_evidence
from modelfc.public_match_evidence import project
from tests import test_btts_research as btts
from tests.team_intelligence_data import fixture_bytes


def generate():
    row = json.loads(Path('tests/fixtures/strategy_picks.json').read_text())['publications'][0]
    with tempfile.TemporaryDirectory() as directory:
        state = Path(directory)/'state'
        record_btts_research(state, btts.forecast(), btts.observation())
        now = btts.NOW + timedelta(minutes=6)
        summary = match_evidence(state, btts.fixture(), as_of=now, history_bytes=fixture_bytes())
        return project(row, summary, now)


if __name__ == '__main__':
    Path('tests/fixtures/public_match_evidence.json').write_text(json.dumps(generate(), indent=2)+'\n')
