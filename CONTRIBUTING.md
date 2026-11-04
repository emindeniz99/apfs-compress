# Contributing

## Commits

Conventional Commits, with a scope, always:

```
<type>(<scope>): <subject>

[body: why, wrapped at 72]

[trailers]
```

- Types: `feat`, `fix`, `docs`, `refactor`, `perf`, `test`, `chore`, `build`, `ci`, `revert`.
- Scopes are areas of this repository: `tool` (`src/apfs_compress`), `tests`, `research`, `docs`, `ci`, `repo`.
- Subject in the imperative, lowercase, no trailing period, the whole header at most 72 characters.
- A body when the why is not in the diff: the problem, the cause, what changed and why, how it was checked.
- An AI assistant that materially wrote a commit is named in a `Co-Authored-By:` trailer, with the model's current name.

## Merging

Merge with a real merge commit (`git merge --no-ff`, or GitHub's "merge" button). Never squash and never rebase-merge: the per-commit history is the record of how the tool was built and measured.

## Checks

```bash
uv sync --locked
uv run ruff format --check && uv run ruff check && uv run ty check --error-on-warning
uv run python -m unittest discover -s tests -v     # macOS on APFS only
```

The tests make a tree in a temporary directory and compress it with `ditto`, so they run only on macOS. CI runs them on a macOS runner; lint and types run anywhere.

A measured finding goes into the README's notes with the script that produced it, under `research/`.

## Releases

release-please keeps one release PR open from the Conventional Commits on `main`; merging it (merge commit, as always) creates the tag and the GitHub Release. Never edit `version.txt` and `__version__` in `src/apfs_compress/__init__.py` by hand: the release PR bumps them. A `fix` makes a patch release, a `feat` a minor one, a `!` or a `BREAKING CHANGE:` footer a major one; `docs`, `test`, `ci` and `chore` commits do not make a release on their own.
