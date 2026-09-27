# Credential inspection safety

Runtime credential audits must never print a complete Docker environment array,
an `.env` file, or a process environment. In particular, do not run commands
that render Docker `.Config.Env` directly in a terminal, CI log, task report, or
Codex tool result.

Use the redacting inspector instead:

```powershell
python scripts/safe_env_inspection.py --container football-api
```

The utility captures Docker metadata in memory and emits only one of these
markers for the allowlisted names:

- `VARIABLE_NAME=********`
- `VARIABLE_NAME=<empty>`
- `VARIABLE_NAME=<missing>`
- `VARIABLE_NAME=******** (duplicate definitions=N)`

Rules for all operational tasks:

1. Query only the credential names needed for the task.
2. Report presence, retirement state, or a non-reversible fingerprint; never a
   value, header, signed URL, or full request configuration.
3. Keep `.env`, dumps, backups, logs, screenshots, and credential exports out of
   Git and outside Docker build contexts.
4. Back up a credential source before mutation, restrict the backup to the
   current Windows user, and never attach it to a task report.
5. Treat unexpected plaintext output as an exposure incident and stop external
   provider calls until affected active credentials are rotated.
