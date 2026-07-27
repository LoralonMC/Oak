# Tests

Standalone check scripts. Each exits non-zero on failure, so they work as-is in
a shell or a CI step.

These import branch modules, which import `discord`, so they need an
environment with the bot's dependencies installed. On a dev box that means
`pip install -r requirements.txt`; against the live container it means:

```sh
scp tests/check_hash.py root@<host>:/var/lib/pterodactyl/volumes/<uuid>/_check.py
ssh root@<host> "docker exec <container> python /home/container/_check.py"
```

`OAK_ROOT` overrides the import root if the repo isn't the parent directory
(that's how these run inside the container, where the code lives at
`/home/container`).

| Script | Covers |
|---|---|
| `check_hash.py` | `hash_config()` reacts to panel-visible config changes only, and ignores credentials, ports and staff roles. Guards against the panel being deleted and reposted on every unrelated edit. |
| `check_errorlog.py` | `_BufferingErrorHandler` filters by level, drops `discord.*` records (feedback-loop guard), never raises out of `emit()`, and bounds its buffer. Plus the dedupe signature. |

## Still to do

Proper pytest suite over the other pure functions: `parse_time_string`,
`_validate_format`, `deep_merge`, `sanitize_name`,
`paginate_application_embed`, and the `custom_id` build/parse round-trip. Plus
a CI workflow running `python -m compileall` across the tree, which would have
caught a missing import that reached production once.
