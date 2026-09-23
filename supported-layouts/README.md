# Supported-layout catalog

This public directory records **structure**, not customer source PDFs, actual
technical values, private file paths or approved-output hashes. The private
golden ledger and approval anchor are deliberately outside this repository.

`CANDIDATE` means an accepted output has been observed, but source regions,
brand/technical classification and a cross-product parameter extractor have
not been proven. It always routes to `NEEDS_AI_REVIEW`.

`SUPPORTED_EXACT` means one or more approved **exact-source** recipes exist.
Only a source with the exact registered SHA, the corresponding frozen recipe,
unaltered baseline, matching assets and matching engine can use deterministic
replay. This does **not** license reuse of another source's coordinates or
engineering parameters. A same-name or similar-looking unknown PDF routes to
`NEEDS_AI_REVIEW`.

`SUPPORTED` is reserved for a generalized extractor with at least two
distinct same-structure source PDFs generated and fully QA-verified without
manual layout edits. Catalog promotion must add the extractor, supported
cases, negative cases and separate regression evidence. No current family is
at this tier.

`scripts/supported_layouts.py` exposes `inspect_source()`, `load_catalog()`
and `preflight_source()`. Probe matches are diagnostic candidates, not
production permission. Unknown or ambiguous ink, additional technical notes,
new PCB/pin/tolerance structures and uncertain supplier branding remain
review-required. No automatic deletion occurs in preflight.
