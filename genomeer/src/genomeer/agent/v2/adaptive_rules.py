"""
genomeer/src/genomeer/agent/v2/adaptive_rules.py
================================================
OrchestrationManager — Règles d'adaptation mid-pipeline.
Gère l'injection de nouveaux steps et les abandons basés sur les signaux de qualité.
"""

import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger("genomeer.orchestration")

ADAPTIVE_RULES = [
    {
        "name": "Fallback to MEGAHIT on low N50",
        "signal": "assembly_n50",
        "operator": "lt",
        "threshold": 1000,
        "condition": "metaspades",
        "action": "inject_step",
        "inject": {
            "title": "Assembly (MEGAHIT fallback)",
            "status": "todo",
            "notes": "metaSPAdes produced low N50. Retrying with MEGAHIT (more robust on low depth).",
            "phase": 2
        }
    },
    {
        "name": "Abort on critically low N50",
        "signal": "assembly_n50",
        "operator": "lt",
        "threshold": 200,
        "condition": None,
        "action": "abort_pipeline",
        "inject": None
    },
    {
        "name": "Abort on critically low classification",
        "signal": "classified_pct",
        "operator": "lt",
        "threshold": 1.0,
        "condition": "kraken2",
        "action": "abort_pipeline",
        "inject": None
    },
    {
        "name": "Re-binning on low completeness",
        "signal": "completeness",
        "operator": "lt",
        "threshold": 50.0,
        "condition": "checkm2",
        "action": "inject_step",
        "inject": {
            "title": "Refine Binning (low completeness)",
            "status": "todo",
            "notes": "MAG completeness < 50%. Injecting refinement step with DAS_Tool or MetaBAT2 --sensitive.",
            "phase": 4
        }
    },
    {
        "name": "Bin cleaning on high contamination",
        "signal": "contamination",
        "operator": "gt",
        "threshold": 10.0,
        "condition": "checkm2",
        "action": "inject_step",
        "inject": {
            "title": "Bin Purge (high contamination)",
            "status": "todo",
            "notes": "MAG contamination > 10%. Injecting bin cleaning / refinement step.",
            "phase": 4
        }
    }
]

class OrchestrationManager:
    """
    Gère les règles d'adaptation du pipeline de manière atomique et non-bloquante.
    """

    @staticmethod
    def evaluate_rules(
        plan: List[Dict[str, Any]], 
        current_idx: int, 
        manifest: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Évalue les règles basées sur les quality_signals et modifie le plan si nécessaire.
        
        Returns:
            - Un dictionnaire de mise à jour de l'état (ex: {"plan": new_plan, "next_step": "finalizer"})
            - Ou None si aucune règle n'a été déclenchée.
        """
        if current_idx >= len(plan):
            return None

        step = plan[current_idx]
        if step.get("status") != "done":
            return None

        qs = manifest.get("quality_signals") or {}
        step_title = step.get("title", "").lower()
        step_code = step.get("code", "").lower()
        step_notes = step.get("notes", "").lower()
        
        new_plan = list(plan)
        triggered_msgs = []
        
        # 1. Évaluation des ADAPTIVE_RULES classiques
        for rule in ADAPTIVE_RULES:
            signal_val = qs.get(rule["signal"])
            if signal_val is None:
                continue
            
            # Condition spécifique à l'outil
            if rule.get("condition") and rule["condition"] not in (step_title + " " + step_code):
                continue
            
            triggered = False
            op = rule["operator"]
            thresh = rule["threshold"]
            
            try:
                val = float(signal_val)
                if op == "lt": triggered = val < thresh
                elif op == "gt": triggered = val > thresh
                elif op == "eq": triggered = val == thresh
                elif op == "lte": triggered = val <= thresh
                elif op == "gte": triggered = val >= thresh
            except (ValueError, TypeError):
                continue

            if triggered:
                if rule["action"] == "inject_step":
                    inj = rule["inject"]
                    # Éviter l'injection multiple du même step
                    if not any(inj["title"].lower() in s.get("title", "").lower() for s in new_plan):
                        logger.info(f"[ADAPTIVE] Triggered {rule['name']}: injecting {inj['title']}")
                        new_plan.insert(current_idx + 1, inj)
                        triggered_msgs.append(
                            f"[ADAPTIVE PLAN] Rule '{rule['name']}' triggered ({rule['signal']}={val}). Injecting step: {inj['title']}."
                        )
                elif rule["action"] == "abort_pipeline":
                    logger.warning(f"[ADAPTIVE] Triggered {rule['name']}: aborting pipeline")
                    return {
                        "next_step": "finalizer",
                        "abort_reason": f"[ADAPTIVE ABORT] {rule['name']} triggered. Pipeline stopped for safety."
                    }

        # 2. Logique spécifique Long-Read (Flye -> Racon -> Medaka)
        is_flye = "flye" in step_title or "flye" in step_code
        is_ont = any(kw in (step_title + step_code + step_notes) 
                    for kw in ("nano", "ont", "nanopore", "long-read", "nano-raw", "nano-hq"))
        
        if is_flye and is_ont:
            has_racon = any("racon" in s.get("title", "").lower() for s in new_plan)
            has_medaka = any("medaka" in s.get("title", "").lower() for s in new_plan)
            
            if not (has_racon or has_medaka):
                logger.info("[ADAPTIVE] Flye+ONT detected. Injecting polishing steps.")
                # Injection en ordre inverse pour obtenir Flye -> Racon1 -> Racon2 -> Medaka
                new_plan.insert(current_idx + 1, {
                    "title": "Run Medaka (ONT Consensus Polishing)",
                    "description": "Polish with Medaka neural-network consensus.",
                    "status": "todo",
                    "phase": 2
                })
                new_plan.insert(current_idx + 1, {
                    "title": "Run Racon Round 2 (ONT Polish)",
                    "description": "Second Racon polishing round.",
                    "status": "todo",
                    "phase": 2
                })
                new_plan.insert(current_idx + 1, {
                    "title": "Run Racon Round 1 (ONT Polish)",
                    "description": "First Racon polishing round.",
                    "status": "todo",
                    "phase": 2
                })
                triggered_msgs.append(
                    "[ADAPTIVE PLAN — LONG-READ] Flye assembly on ONT reads detected. Injecting Racon (x2) -> Medaka pipeline."
                )

        if triggered_msgs:
            return {
                "plan": new_plan,
                "adaptation_messages": triggered_msgs
            }
        
        return None
