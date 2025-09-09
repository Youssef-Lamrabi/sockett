from typing import Dict, Type
from .base import Tool
from .builtin.gc_content import GCContent
from .builtin.kmers import KMerCount

_REGISTRY: Dict[str, Type[Tool]] = {
    "gc_content": GCContent,
    "kmer_count": KMerCount,
}

def list_tools():
    return [cls() for cls in _REGISTRY.values()]

def get_tool(name: str) -> Tool:
    return _REGISTRY[name]()
