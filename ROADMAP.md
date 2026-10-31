# Roadmap

- If a later macOS lets `ditto` (or another Apple tool) write LZBITMAP or a new type such as LZMESH, measure it with `research/` and recompress.
- APFS clone dedupe of byte-identical files across Unity editors (14.3 GiB of 86.6 GiB measured 2026-10-31). It is not done here; it would need its own verified copy and swap flow.
- A `copy --test CMD` hook that runs a functional check (for example an editor test suite) against the copy before the report is written.
