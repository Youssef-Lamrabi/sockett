import pytest
import os
import re
import time
import json
import tempfile
from pathlib import Path

class TestNoUndefinedStatus:
    """BUG 2: 'status' ne doit pas être référencé dans _planner/_generator."""

    def test_planner_source_has_no_status_variable(self):
        import ast
        import inspect
        try:
            from genomeer.agent.v2.BioAgent import BioAgent
        except ImportError:
            pytest.skip("BioAgent not importable in this environment")

        source = inspect.getsource(BioAgent)
        tree = ast.parse(source)

        planner_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_planner":
                planner_node = node
                break
        assert planner_node is not None, "Could not find _planner function in BioAgent"

        # Trouver toutes les assignations de 'status'
        assigned_vars = set()
        for node in ast.walk(planner_node):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        assigned_vars.add(t.id)
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                if hasattr(node, 'target') and isinstance(node.target, ast.Name):
                    assigned_vars.add(node.target.id)

        # Trouver toutes les utilisations de 'status'
        used_names = set()
        for node in ast.walk(planner_node):
            if isinstance(node, ast.Name) and node.id == "status":
                used_names.add("status")

        if "status" in used_names:
            assert "status" in assigned_vars, (
                "Variable 'status' is READ in _planner but never ASSIGNED. "
                "This is the NameError bug — the Fix 5 checkpoint block references "
                "'status' which only exists in _observer."
            )

    def test_generator_source_has_no_orphan_status(self):
        import ast
        import inspect
        try:
            from genomeer.agent.v2.BioAgent import BioAgent
        except ImportError:
            pytest.skip("BioAgent not importable in this environment")

        source = inspect.getsource(BioAgent)
        tree = ast.parse(source)

        generator_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_generator":
                generator_node = node
                break
        assert generator_node is not None, "Could not find _generator function in BioAgent"

        assigned = set()
        for node in ast.walk(generator_node):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        assigned.add(t.id)

        used = set()
        for node in ast.walk(generator_node):
            if isinstance(node, ast.Name) and node.id == "status":
                used.add("status")

        if "status" in used:
            assert "status" in assigned, (
                "Variable 'status' is READ in _generator but never ASSIGNED locally. "
                "Remove the Fix 5/Fix 8/Fix 9 blocks that reference 'status' from _generator."
            )


# ===========================================================================
# BUG #3 — Checkpoint session_id correct (jamais "unknown")
# ===========================================================================

