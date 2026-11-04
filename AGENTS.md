# Rules for AI assistants

This is the one source of truth for coding agents (Codex reads it natively; `CLAUDE.md` imports it with `@AGENTS.md`). `CONTRIBUTING.md` is the human-facing version; where they overlap, they agree.

## Commits and merges

- Conventional Commits with a scope, always: `feat(tool): …`, `fix(tests): …`, `docs(repo): …`. Scopes: `tool`, `tests`, `research`, `docs`, `ci`, `repo`. Imperative subject, lowercase, no trailing period, header at most 72 characters; a body that explains why.
- Name the model in a `Co-Authored-By:` trailer on every commit you materially wrote. Keep the name current.
- Merge with a real merge commit. Never squash, never rebase-merge, never force-push a shared branch, never amend a pushed commit.

## The tool

- It never compresses in place. `copy` makes a compressed copy beside the original and verifies it; only `swap`, `rollback` and `finish` touch the original, and the user runs those. Keep that split: no command may delete or replace an original the user has not asked to.
- Verification compares content, metadata and code signatures. A difference that is known and harmless (a symlink's mtime, `com.apple.provenance`, `com.apple.macl`) is reported, not failed, and listed in the README. Any new exception is added there with the measurement that justifies it.
- Standard library only, Python 3.9 and later. Lint and types with ruff and ty before committing; the unit tests need macOS on APFS.
- A measured result goes into the README's notes with the script that produced it, under `research/`.

## Releases

- release-please owns the version: never edit `version.txt` and `__version__` in `src/apfs_compress/__init__.py` by hand, and never create a tag or a release by hand; merge the release PR. Its commit types decide the bump, so a behavior change is a `feat` or a `fix`, never a `chore`.

## Privacy

No file names, paths or app identifiers from the user's machine beyond what the README's measurements already name, and nothing that identifies an account.
