# Development Guide

Use the repository venv:

```powershell
.\qt\.venv\Scripts\python.exe -m pytest -q
.\qt\.venv\Scripts\ruff.exe check .
```

Safe research commands:

```powershell
.\qt\.venv\Scripts\python.exe scripts\cost_decomposition.py --input data\store\v4\mt5_XAUUSD_M5_a052314f19605be8.parquet --output-dir reports_v4\costs --registry experiments\registry.jsonl
.\qt\.venv\Scripts\python.exe scripts\dry_run.py --help
```

Do not run holdout access, broad searches, data downloads, or any execution adapter without an explicit experiment record and output directory. Never print environment files, credentials, or broker connection details. Keep generated datasets and reports out of commits unless they are deliberate provenance artifacts.
