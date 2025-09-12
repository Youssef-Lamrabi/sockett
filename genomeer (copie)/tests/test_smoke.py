from genomeer.agent.orchestrator import run_turn

def test_turn():
    msgs = [{"role":"user","content":"Compute GC% of ACGT and count 2-mers."}]
    out = run_turn(msgs, model="llama3.1:8b")  # any local model
    assert "GC" in out["answer"] or out["executions"], out
