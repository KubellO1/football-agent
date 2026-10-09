# Safe Docker diagnostics

Never print or log a container's Docker `Config.Env` array. It contains
plaintext runtime credentials.

To verify whether approved credential variables are configured, use:

```powershell
python scripts/safe_env_inspection.py --container football-api
```

The helper captures Docker metadata internally and emits only these markers:

- `VARIABLE_NAME=REDACTED` when a secret is configured.
- `VARIABLE_NAME=EMPTY` when it is empty or absent.

Do not replace this helper with `docker inspect` templates that select
`.Config.Env`, or with `docker compose config`, in logs, reports, CI, or task
diagnostics.
