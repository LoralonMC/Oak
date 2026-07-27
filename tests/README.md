# Tests

Pytest suite over the framework and branch logic that can be exercised without
a live Discord connection. Run from the repo root:

```sh
pip install -r requirements.txt pytest
pytest -q
```

`conftest.py` puts the repo root on `sys.path`, so no install step is needed.
`OAK_ROOT` overrides that if the code lives elsewhere (that's how these run
inside the bot container, where it's `/home/container`).

CI runs the same thing on 3.10 (matching the container image) plus a
`compileall` pass over every module, which catches import and syntax errors in
files no test touches.

## Coverage

| File | Covers |
|---|---|
| `test_utils.py` | `sanitize_text`, `truncate`, `truncate_for_embed_field`, `paginate`, and `deep_merge` — including that merged config doesn't alias its inputs, since a shared nested reference would let one branch's config edit mutate another's defaults. |
| `test_interactions.py` | `custom_id` build/parse round-trip, rejection of malformed ids and values, and the 100-char limit. Persistent views depend on these surviving a restart. |
| `test_tickets_helpers.py` | `parse_time_string`, `sanitize_name`, `member_can_manage_category`, `validate_config`, and `hash_config` — the last asserting the panel is reposted only for changes that alter what it renders. |
| `test_application_helpers.py` | `is_staff`, answer-quality checks, question loading, and `paginate_application_embed` against Discord's field-count, field-length and total-size limits, including that pagination terminates on pathological input. |
| `test_errorlog.py` | The buffering handler: level filtering, traceback capture, the `discord.*` feedback-loop guard, bounded buffer, and that `emit()` never raises. It sits in the logging path, so a bug there breaks every log call. |
| `test_metrics.py` | Persistence round-trip and that a corrupt, wrongly-shaped or hand-edited metrics file can't stop the bot starting. |
| `test_status_channels.py` | `_validate_format` rejecting attribute and index access in config-supplied format strings. |

## Not covered

Anything needing a live gateway connection: view callbacks, modal submission,
the loader's cog lifecycle, and the branch background tasks. Those are still
verified by deploying and reading the startup log.
