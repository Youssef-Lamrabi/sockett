import pytest
from genomeer.utils.security import check_bash_script

def test_security_halt_condition_allowed():
    """Vérifie que halt_condition=True n'est pas bloqué (faux positif évité)."""
    script = "halt_condition=True\nif halt_condition:\n    print('stop')"
    is_safe, reason = check_bash_script(script)
    assert is_safe is True
    assert reason == "ok"

def test_security_shutdown_blocked():
    """Vérifie que shutdown seul sur une ligne est bloqué."""
    script = "echo 'starting'\nshutdown -h now\necho 'done'"
    is_safe, reason = check_bash_script(script)
    assert is_safe is False
    assert "system shutdown/reboot command" in reason

def test_security_shutdown_in_comment_allowed():
    """Vérifie que shutdown dans un commentaire est autorisé."""
    script = "# This script does NOT call shutdown\necho 'hello'"
    is_safe, reason = check_bash_script(script)
    assert is_safe is True
    assert reason == "ok"

def test_security_shutdown_in_variable_allowed():
    """Vérifie que shutdown_flag=True est autorisé."""
    script = "shutdown_flag=True\nif [ \"$shutdown_flag\" = \"True\" ]; then echo 'skip'; fi"
    is_safe, reason = check_bash_script(script)
    assert is_safe is True
    assert reason == "ok"
