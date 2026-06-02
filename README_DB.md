# Local SQLite storage

The app now uses SQLite (file-based, no installation) instead of Timescale.

## Defaults
- File path: `storage.db` in project root (configurable via `SQLITE_PATH` env var).
- Tables auto-created on startup: `ticks`, `ohlc`, `signals`.

## Env
```
SQLITE_PATH=storage.db
```

## Notes
- SQLite is single-file and fine for local use; for multi-process writes, keep to a single writer process.
- If you change the path, ensure the process has write permissions.
