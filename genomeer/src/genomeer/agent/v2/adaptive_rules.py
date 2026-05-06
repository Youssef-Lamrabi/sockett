"""
genomeer/src/genomeer/agent/v2/adaptive_rules.py
================================================
Règles d'adaptation mid-pipeline basées sur les quality_signals.
"""

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
