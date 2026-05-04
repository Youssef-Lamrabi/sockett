"""
tests/e2e/test_assembly_flow.py
================================
Test End-to-End: Valide le routing complet du pipeline Genomeer (planner ->
orchestrator -> finalizer) avec un vrai fichier FASTA et un LLM mocké.

Le LLM n'est jamais appelé en réseau — on patche agent._llm_invoke.
Le fichier FASTA utilisé est tests/e2e/data/ecoli.fasta (fragment 16S mt-human
téléchargé depuis minimap2 test data).
"""
import os
import pytest
from pathlib import Path
from langchain_core.messages import AIMessage

DATA_DIR = Path(__file__).parent / "data"


def make_mock_llm_invoke(node_responses: dict):
    """Factory : renvoie une fonction _llm_invoke qui se comporte comme un mock."""
    def _mock(self_agent, node, expected_model, messages, **kwargs):
        content = node_responses.get(node, "OK")
        return AIMessage(content=content)
    return _mock


# ---------------------------------------------------------------------------
# Fixture : chemin vers le FASTA réel (téléchargé lors de la création du repo)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def ecoli_fasta():
    path = DATA_DIR / "ecoli.fasta"
    if not path.exists():
        # Fallback minimal si le téléchargement a raté
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(">seq1_ecoli_fallback\nATGCATGCATGCATGCATGC\n")
    return path


# ---------------------------------------------------------------------------
# Test E2E principal
# ---------------------------------------------------------------------------
def test_e2e_pipeline_routing(monkeypatch, ecoli_fasta, tmp_path):
    """
    Valide que BioAgent.go() parcourt le graphe LangGraph sans planter et
    retourne un état final cohérent avec le fichier FASTA fourni.
    """
    from genomeer.agent.v2.BioAgent import BioAgent

    os.environ["GENOMEER_RAG_OFFLINE"] = "1"

    # Réponses LLM mockées par nœud
    # IMPORTANT: le planner attend le format checklist markdown : - [ ] Title <next:ORCHESTRATOR>
    LLM_RESPONSES = {
        "planner":    "- [ ] Check FASTA Input\n- [ ] Run QC\n<next:ORCHESTRATOR>",
        "qa":         "APPROVED",
        "generator":  '<code>\nmanifest["quality_signals"] = {"n50_bp": 50000, "classified_pct": 99.0}\n</code>',
        "observer":   "Step completed successfully.",
        "finalizer":  "## Genomeer Report\nAnalyse réussie sur le fichier FASTA fourni.",
        # L'orchestrateur ne fait pas d'appel LLM — son routing est conditionnel sur next_step
    }

    # Patcher _llm_invoke sur la classe (avant instanciation)
    monkeypatch.setattr(
        BioAgent, "_llm_invoke",
        make_mock_llm_invoke(LLM_RESPONSES),
    )

    # Patcher _prepare_resources_for_retrieval pour court-circuiter FAISS/langchain_openai
    def _mock_prepare_resources(self_agent, prompt):
        return {"tools": [], "data_lake": [], "libraries": []}
    monkeypatch.setattr(BioAgent, "_prepare_resources_for_retrieval", _mock_prepare_resources)

    agent = BioAgent()

    # Exécuter le pipeline (mode 'dev' = synchrone, retourne le dict d'état final)
    result = agent.go(
        prompt=f"Analyse ce génome bactérien : {ecoli_fasta}",
        attachments=[str(ecoli_fasta)],
        mode="dev",
    )

    # ── Assertions de routing ──────────────────────────────────────────────
    assert result is not None, "go() ne doit pas retourner None"
    
    # go() retourne (messages: list[str], final_content: str)
    assert isinstance(result, tuple) and len(result) == 2, (
        f"go() doit retourner un tuple (messages, final_content), obtenu: {type(result)}"
    )
    messages, final_content = result
    
    # Le pipeline doit avoir généré plusieurs messages
    assert isinstance(messages, list), f"Premier élément doit être une liste de messages, obtenu: {type(messages)}"
    assert len(messages) >= 2, f"Au moins 2 messages attendus (plan + réponse), obtenu: {len(messages)}"
    
    # Vérifier que le planner a bien produit un plan (format checklist)
    plan_msgs = [m for m in messages if "- [ ]" in m or "- [x]" in m]
    assert len(plan_msgs) >= 1, "Au moins un message contenant une checklist attendu depuis le planner"
    
    # Vérifier la réponse finale
    assert isinstance(final_content, str), f"final_content doit être une str, obtenu: {type(final_content)}"
    assert len(final_content) > 0, "final_content ne doit pas être vide"


def test_e2e_fasta_file_readable(ecoli_fasta):
    """Vérifie que le fichier FASTA de test est lisible et bien formaté."""
    content = ecoli_fasta.read_text(errors="replace")
    assert content.startswith(">"), f"FASTA doit commencer par '>': {content[:40]!r}"
    lines = [l for l in content.splitlines() if l and not l.startswith(">")]
    assert len(lines) >= 1, "FASTA doit avoir au moins une ligne de séquence"
    total_bases = sum(len(l) for l in lines)
    assert total_bases >= 100, f"Séquence trop courte ({total_bases} bp), vérifier le téléchargement"
    print(f"\nFASTA OK: {ecoli_fasta.name}, {total_bases} bp total")
