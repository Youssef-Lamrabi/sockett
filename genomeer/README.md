# Genomeer v3

## Environment Variables / Resource Quotas

Genomeer v2 enforces strict resource limits on spawned subprocesses (via `resource.setrlimit`) to prevent single tools from consuming all system resources or causing "fork bombs".

### Quota Settings

- `GENOMEER_MAX_RAM_GB` : Maximum RAM (in Gigabytes) allowed for a single subprocess. Default is `16`. If a tool exceeds this limit, it may crash and a `MemoryError` will be logged.
- `GENOMEER_MAX_CPU_SECONDS` : Maximum CPU time allowed (soft/hard limit). Typically determined dynamically based on the tool's estimated timeout.
- The maximum number of child processes (`RLIMIT_NPROC`) is hardcoded to `512` to mitigate fork bombs.

### Recommendations by Tool

For heavier bioinformatics tools, consider increasing the memory quota:

- **metaSPAdes**: `GENOMEER_MAX_RAM_GB=64` (Requires substantial memory for De Bruijn graph assembly)
- **Kraken2**: `GENOMEER_MAX_RAM_GB=32` (Required for standard/large Kraken databases loaded into RAM)
- **fastp**: `GENOMEER_MAX_RAM_GB=4` (Lightweight, fits comfortably within the default)

## Mise à jour des bases de données

Pour garantir des résultats biologiques pertinents (notamment pour la détection de la résistance aux antibiotiques via CARD et l'assignation taxonomique via GTDB/Kraken2), il est impératif de garder les bases de données à jour.

### 1. Kraken2 Standard Database
```bash
kraken2-build --standard --threads 8 --db /path/to/kraken2_standard
```

### 2. GTDB-Tk Reference Data
```bash
wget https://data.gtdb.ecogenomic.org/releases/latest/auxillary_files/gtdbtk_data.tar.gz
tar -xvzf gtdbtk_data.tar.gz -C /path/to/gtdbtk_db
export GTDBTK_DATA_PATH=/path/to/gtdbtk_db
```

### 3. CARD (RGI)
```bash
rgi load --card_json /path/to/card.json --local
```

### 4. Régénération des bundles RAG (BioRAG)
Les bundles JSON utilisés pour le contexte BioRAG (`card_top500.json`, `kegg_core_pathways.json`) doivent être rafraîchis tous les 180 jours. Vous pouvez lancer le mode de mise à jour RAG :
```bash
python -m genomeer.model.bio_rag --update-bundles
```
