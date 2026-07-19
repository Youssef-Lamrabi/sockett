#!/usr/bin/env python3
"""
Regenerate the Tools panel catalog (static/tools_catalog.json) from the agent's REAL
tool descriptions (module2api) + a fixed database list, and print a COVERAGE REPORT so
CLI tools that are installed/known to the retriever but have NO description (=> invisible
in the panel) are surfaced. Run this whenever tools change:

    <bio-agent-env1>/bin/python regen_tools_catalog.py

A tool WITH a description = user-facing (shown + @-selectable). A tool WITHOUT one is
treated as an internal dependency (e.g. samtools, bwa) and intentionally left out.
"""
import importlib, os, json, re, sys

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "tools_catalog.json")

CAT_MAP = {
    "metagenomics": "Metagenomics", "metagenomics_db": "Database", "ncbi": "NCBI / Web",
    "genomics": "Genomics", "viromics": "Viromics", "basic": "Utilities", "artifacts": "Workspace",
}
ICON_MAP = {
    "Metagenomics": "fa-bacteria", "Database": "fa-database", "NCBI / Web": "fa-globe",
    "Genomics": "fa-dna", "Viromics": "fa-virus", "Utilities": "fa-screwdriver-wrench",
    "Workspace": "fa-folder-open",
}

DATABASES = [
    ("kraken2", "Taxonomic read classification (bacteria/archaea/viral)"),
    ("eggNOG", "Orthology & functional annotation database"),
    ("CARD", "Antibiotic resistance gene reference (RGI)"),
    ("AMRFinderPlus DB", "NCBI AMR & virulence reference"),
    ("antiSMASH DB", "Biosynthetic gene cluster references"),
    ("MOB-suite plasmids", "Plasmid reference for mobility typing"),
    ("dbCAN", "Carbohydrate-active enzyme (CAZyme) HMMs"),
    ("BLAST DB (local)", "Local BLAST database at ~/tools/BLAST"),
    ("NCBI Taxonomy", "Taxon names & lineage lookup"),
    ("NCBI Entrez", "Sequence & metadata (nuccore/assembly/SRA)"),
    ("SILVA", "rRNA (16S/18S/23S) reference"),
    ("GTDB", "Genome Taxonomy Database"),
    ("MGnify", "EBI metagenomics studies & samples"),
    ("SRA", "Sequencing reads archive"),
    ("UniProt", "Protein sequences & function"),
    ("KEGG", "Pathways & orthology"),
    ("VFDB", "Virulence factor database"),
    ("CAZy", "Carbohydrate-active enzyme families"),
    ("Europe PMC", "Biomedical literature"),
    ("Wikipedia", "General background context"),
]

# Binaries known to the retriever that are pure INTERNAL dependencies — intentionally not
# shown as user-facing tools (they are invoked by other tools, not @-selected on their own).
DEP_DENYLIST = {"samtools", "bwa", "bowtie2", "fastani", "mob_suite", "esearch", "efetch",
                "blastn", "makeblastdb", "diamond", "mash"}


def short(desc):
    d = re.sub(r'^\s*(\[[^\]]*\]\s*)+', '', desc or '').strip()
    d = re.split(r'\s+(?:Command|CMD|Usage|Inputs?|Output|INVOKE|RELIABILITY|TWO MODES|CRITICAL)\b', d, 1)[0]
    s = re.split(r'(?<=[.])\s', d, 1)[0].strip()
    s = re.sub(r'\s+', ' ', s)
    return (s[:102].rstrip() + "…") if len(s) > 105 else s


def pretty(name):
    return name[4:] if name.startswith("run_") else name


def main():
    import genomeer.tools.description as D
    from genomeer.model.retriever import _CLI_TOOL_BINARIES, _available_cli_tools
    avail = _available_cli_tools()

    tools = []
    described = set()
    for f in os.listdir(D.__path__[0]):
        if not f.endswith(".py") or f.startswith("__"):
            continue
        mod = importlib.import_module(f"genomeer.tools.description.{f[:-3]}")
        d = getattr(mod, "description", None)
        if not isinstance(d, list):
            continue
        cat = CAT_MAP.get(f[:-3], f[:-3])
        for t in d:
            name = t.get("name", "")
            key = name.replace("run_", "").lower()
            described.add(key)
            installed = True
            for cli in _CLI_TOOL_BINARIES:
                if cli == key:
                    installed = cli in avail
                    break
            tools.append({"name": pretty(name), "category": cat, "icon": ICON_MAP.get(cat, "fa-flask"),
                          "description": short(t.get("description", "")), "installed": installed})

    # Dedupe by name (keep first occurrence, preserve order). A tool can be
    # declared in more than one description module (e.g. fetch_sra_reads lives in
    # the isolated sra.py AND the dormant metagenomics_db.py) — the panel must
    # never list the same tool twice.
    _seen = set()
    _deduped = []
    for t in tools:
        if t["name"] in _seen:
            continue
        _seen.add(t["name"])
        _deduped.append(t)
    tools = _deduped

    databases = [{"name": n, "category": "Database", "icon": "fa-database", "description": d, "installed": True}
                 for n, d in DATABASES]

    json.dump({"tools": tools, "databases": databases}, open(OUT, "w"), indent=1)
    print(f"WROTE {OUT}: {len(tools)} tools + {len(databases)} databases")

    # --- COVERAGE REPORT ---
    def _covered(k):
        # a tool is covered if its name loosely matches any described name (handles
        # aliases: amrfinderplus~amrfinder, eggnog-mapper~eggnog, metaphlan~metaphlan4,
        # run_dbcan~dbcan, run_gget_virus~gget_virus)
        kk = k.replace("run_", "")
        return any(kk == dn or kk in dn or dn in kk for dn in described)

    missing = sorted(k for k in _CLI_TOOL_BINARIES
                     if k in avail and not _covered(k) and k not in DEP_DENYLIST)
    deps = sorted(k for k in _CLI_TOOL_BINARIES if k in avail and k in DEP_DENYLIST)
    print("\n=== COVERAGE REPORT ===")
    if missing:
        print("[!] INSTALLED + retriever-known but NO description (invisible in panel) - add a description if user-facing:")
        print("   " + ", ".join(missing))
    else:
        print("[ok] Every installed retriever-known tool has a description (no gap).")
    print(f"[i] Intentionally hidden internal dependencies: {', '.join(deps) or '(none)'}")


if __name__ == "__main__":
    sys.exit(main())
