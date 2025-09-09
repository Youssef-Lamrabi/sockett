from typing import Dict, Any
from ..base import Tool

class KMerCount(Tool):
    name = "kmer_count"
    description = "Count k-mers in a DNA sequence"

    def schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "sequence": {"type": "string"},
                    "k": {"type": "integer", "minimum": 1, "maximum": 15},
                },
                "required": ["sequence", "k"],
            },
        }

    def run(self, **kwargs) -> Dict[str, Any]:
        seq = kwargs["sequence"].upper()
        k = int(kwargs["k"])
        counts = {}
        for i in range(0, max(0, len(seq) - k + 1)):
            km = seq[i:i+k]
            counts[km] = counts.get(km, 0) + 1
        return {"k": k, "counts": counts}
