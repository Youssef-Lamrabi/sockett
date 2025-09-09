from typing import Dict, Any
from ..base import Tool

class GCContent(Tool):
    name = "gc_content"
    description = "Compute GC content for a DNA sequence"

    def schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "sequence": {"type": "string", "description": "DNA sequence (ACGT)"},
                },
                "required": ["sequence"],
            },
        }

    def run(self, **kwargs) -> Dict[str, Any]:
        seq = kwargs["sequence"].upper()
        gc = sum(1 for c in seq if c in "GC")
        pct = (gc / max(1, len(seq))) * 100.0
        return {"gc_percent": round(pct, 3), "length": len(seq)}
