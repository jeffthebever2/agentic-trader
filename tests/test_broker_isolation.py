import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web.api.fidelity import _fidelity_state_path
from web.api.webull_portfolio import _wb_state_path


def test_broker_session_paths_are_per_user():
    first = "first@example.com"
    second = "second@example.com"

    assert _fidelity_state_path(first) != _fidelity_state_path(second)
    assert _wb_state_path(first) != _wb_state_path(second)
    assert _fidelity_state_path(first).name != ".fidelity_session.json"


def test_broker_session_paths_normalize_email_case():
    assert _fidelity_state_path("USER@EXAMPLE.COM") == _fidelity_state_path("user@example.com")
    assert _wb_state_path("USER@EXAMPLE.COM") == _wb_state_path("user@example.com")
