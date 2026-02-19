# Oak Comprehensive Review — `refactor/oak-framework`

Date: 2026-02-19  
Reviewer: Codex (GPT-5.2-Codex)

## Scope and method

I reviewed the current checked-out repository state and focused on:

1. Framework core (`oak/`)
2. Plugin loading lifecycle
3. Interaction/event/database infrastructure
4. Operational readiness and maintainability
5. Branch-level quality signals (lint/static checks)

Because this checkout only contains a single local branch (`work`), I could not perform a true branch-to-branch diff against a local `refactor/oak-framework` ref.

## Commands run

- `git branch -a`
- `python -m compileall -q .`
- `ruff check .`

## Executive summary

The architecture is strong and coherent: branch manifests, dependency-aware loading, scoped context injection, and branch lifecycle hooks are all cleanly structured.

The main risks are **operational correctness** (edge-case behavior in manifest/interaction parsing and branch discovery conflicts) and **maintainability debt** (36 lint findings, many low severity but noisy).

No syntax-level breakages were found (`compileall` passed).

## Findings

### 1) Branch ID collisions are silently overwritten (High)

`BranchLoader.discover()` indexes manifests by `manifest.id`, but does not detect duplicate IDs across folders. A later branch can silently replace an earlier one in `_manifests`/`_paths`, creating non-obvious runtime behavior.

**Impact:** incorrect branch loaded, hard-to-debug production behavior, possible accidental shadowing.

**Recommendation:** fail discovery on duplicates and report both folder paths.

---

### 2) Interaction parser accepts malformed custom IDs (Medium)

`InteractionRouter.parse()` splits by `:` and reads only `parts[3]` for value. IDs with extra segments (`oak:x:y:z:extra`) are accepted and truncated logically.

**Impact:** ambiguity, unsafe assumptions for handlers, harder forensic debugging.

**Recommendation:** enforce exact lengths (`3` or `4` segments), validate each segment consistently with builder constraints.

---

### 3) `load_all()` suppresses config parse errors and continues (Medium)

`BranchLoader.load_all()` catches all exceptions when peeking config `enabled` and silently continues.

**Impact:** malformed config may still attempt load with defaults/partial behavior, reducing operator visibility.

**Recommendation:** log a warning/error with branch ID and config path when parse fails.

---

### 4) Prefix command content is logged verbatim (Medium)

`OakBot.on_message()` logs raw command content prefix (`message.content[:50]`).

**Impact:** potential sensitive data leakage into logs if users pass tokens/credentials via prefix commands.

**Recommendation:** redact arguments or log command name only.

---

### 5) Lifecycle robustness is generally good (Strength)

The framework cleanly bridges discord.py cog lifecycle to `on_enable`/`on_disable`, dispatches one-time `on_ready`, and unloads branches during shutdown.

**Value:** predictable extension model and easier branch isolation.

---

### 6) Static quality debt is measurable and fixable (Medium)

`ruff check .` reports 36 issues (unused imports/variables, unnecessary f-strings, etc.). Most are auto-fixable and low-risk.

**Impact:** signal-to-noise degradation, harder code review, reduced confidence in stricter checks.

**Recommendation:** run `ruff check . --fix` in a dedicated hygiene PR, then gate CI with lint.

## Priority action plan

1. **P0**: Add duplicate manifest ID detection in discovery.
2. **P0**: Harden interaction parsing to reject malformed segment counts.
3. **P1**: Improve config parse observability in `load_all()`.
4. **P1**: Redact prefix command logs.
5. **P2**: Execute lint cleanup and enforce CI quality gates.

## Suggested next review phase (if you want a Part 2)

- Deep audit of high-complexity branches (`tickets`, `application`, `shopkeepers`) for permission boundaries, race conditions, and data consistency.
- Hot-reload reliability tests (load/unload/reload loops under interaction load).
- SQLite contention profiling with concurrent write-heavy branch operations.
