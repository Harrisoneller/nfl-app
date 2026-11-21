"""The materialized eligible set matches the hash written into the prereg.

Reads the committed key file only. No download, no model, no score.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEYS = ROOT / "research" / "player_refit" / "eligible" / "eligible_keys.txt"
PREREG = ROOT / "research" / "player_refit" / "PREREG.md"

EXPECTED_N = 70094
EXPECTED_SHA = "ccfbb2bee685f3f4e6f3bfb416ed9dd71b195716a63f2c2cc2eb66927c688d23"


def test_eligible_keys_match_the_prereg_pin():
    text = KEYS.read_text()
    lines = text.splitlines()
    assert lines == sorted(lines)
    assert all(line.count("|") == 5 for line in lines)
    assert len(lines) == EXPECTED_N
    assert hashlib.sha256(text.encode()).hexdigest() == EXPECTED_SHA
    body = PREREG.read_text()
    assert f"eligible_set_n: {EXPECTED_N}" in body
    assert f"eligible_set_sha256: {EXPECTED_SHA}" in body
    assert "2018" not in {line.split("|", 1)[0] for line in lines}
