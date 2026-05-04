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
