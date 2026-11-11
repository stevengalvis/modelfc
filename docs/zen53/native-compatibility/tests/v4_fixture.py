"""Synthetic v4 observations only, never the real VPS snapshot."""
import json
from pathlib import Path

def complete_synthetic():
    return json.loads(Path(__file__).with_name('v4_synthetic.json').read_bytes())
