"""The shared parity fixture (frontend-react/src/lib/reconciliation_parity.json) must be exactly what the backend
rules produce today. If this fails after a deliberate rule change, regenerate it with
scripts/reconciliation_parity.py; the browser mirror's test will then show whether roadAccess.js still agrees."""
import json
from pathlib import Path

import pytest

from scripts.reconciliation_parity import CASES, OUT, backend_result


def test_fixture_exists_and_covers_every_case():
    data = json.loads(Path(OUT).read_text(encoding="utf-8"))
    assert set(data["cases"]) == set(CASES)


@pytest.mark.parametrize("name", sorted(CASES))
def test_backend_still_produces_the_fixture(name):
    data = json.loads(Path(OUT).read_text(encoding="utf-8"))
    t, raw = CASES[name]
    assert backend_result(t, raw) == data["cases"][name]["expected"]
