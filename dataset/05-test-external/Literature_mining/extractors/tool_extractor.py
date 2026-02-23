import re
from typing import List, Optional, Dict


# Tool dictionary


TOOLS = {
    "assembly": ["spades", "megahit", "idba", "ray-meta", "velvet"],
    "binning": ["metabat", "maxbin", "concoct", "vamb"],
    "taxonomy": ["kraken", "kraken2", "bracken", "metaphlan", "centrifuge"],
    "annotation": ["prokka", "eggnog", "kofam", "interproscan", "prodigal"],
    "mapping": ["bowtie2", "bwa", "minimap2"],
    "quality": ["fastqc", "trimmomatic", "cutadapt"],
    "mag_qc": ["checkm", "gtdb-tk", "drep"],
}



# Tool extraction


def extract_tools(text: str) -> List[str]:
    """Return a flat list of detected metagenomics tools."""
    if not text:
        return []

    text_lower = text.lower()
    found = set()

    for tools in TOOLS.values():
        for tool in tools:
            pattern = r"\b" + re.escape(tool) + r"\b"
            if re.search(pattern, text_lower):
                found.add(tool)

    return sorted(found)



# MAG count extraction


MAG_PATTERNS = [
    r"(\d+)\s+MAGs?",
    r"(\d+)\s+metagenome-assembled genomes",
    r"recovered\s+(\d+)\s+bins?",
]

def extract_mag_count(text: str) -> Optional[int]:
    """Extract MAG count from text."""
    if not text:
        return None

    for pattern in MAG_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))

    return None



# Assembly count extraction

ASSEMBLY_PATTERNS = [
    r"assembled\s+(\d+)\s+contigs",
    r"(\d+)\s+assemblies",
]

def extract_assembly_count(text: str) -> Optional[int]:
    """Extract the number of assemblies or contigs from text."""
    if not text:
        return None

    for pattern in ASSEMBLY_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))

    return None
