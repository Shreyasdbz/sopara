# Dependency license policy

`allowed-licenses.txt` contains the exact license strings emitted by the locked Python and web dependency graphs. `scripts/check_licenses.py` fails on missing or unknown metadata.

The allowlist includes reviewed weak-copyleft and attribution-bearing dependencies needed by the approved stack:

- `LGPL-3.0-only` is emitted by psycopg 3 packages used as dynamically imported Python dependencies;
- `CC-BY-4.0` is emitted by browser-compatibility data;
- `OFL-1.1` is emitted by bundled font packages.

An allowlist match is an admission control, not a substitute for preserving upstream notices or reviewing a new dependency's use and distribution model.
