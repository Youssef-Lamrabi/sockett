import pytest
import threading
from genomeer.utils.helper import run_bash_script
from genomeer.config import settings

def test_run_bash_script_host_fallback_timeout():
    """
    Test that running a bash script on host (env_name=None) that times out
    returns a proper error string and does not crash with a NameError.
    """
    script = """#!/bin/bash
sleep 10
echo "Done"
"""
    # Temporarily override the global timeout setting
    old_timeout = settings.timeout_seconds
    settings.timeout_seconds = 1.0

    try:
        # P1-A.3: The timeout mechanism uses cancel_event set by run_with_timeout or similar
        cancel_event = threading.Event()
        
        # Start a thread to trigger cancel_event after 0.5 seconds
        def canceller():
            cancel_event.set()
        
        t = threading.Timer(0.5, canceller)
        t.start()
        
        result = run_bash_script(
            script=script,
            env_name=None,
            cancel_event=cancel_event
        )
        t.cancel()
        
        # Check that we got an error indicating a timeout/kill rather than NameError crashing
        assert "Error running Bash script" in result or "Timeout" in result or "[TIMEOUT]" in result
    finally:
        settings.timeout_seconds = old_timeout
