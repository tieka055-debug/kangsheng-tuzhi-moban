# Supported-layout catalog

This public directory records **structure**, not customer source PDFs, actual
technical values, private file paths or approved-output hashes. The private
golden ledger and approval anchor are deliberately outside this repository.

`CANDIDATE` means an accepted output has been observed, but source regions,
brand/technical classification and a cross-product parameter extractor have
not been proven. It always routes to `NEEDS_AI_REVIEW`.

Before any **new-source** `EXACT`, `SUPPORTED_COMPATIBLE`, `AI_LOCAL_REVIEW` or
`NEW_LAYOUT` candidate can be generated as a deliverable, the complete,
uncropped supplier page must pass `SOURCE_INVENTORY_GATE`. Selected-group copy
checks are insufficient: every technical object must be carried or accounted
for by an approved field-by-field `AUTHORIZED_TRANSFORM` ledger. Only reviewed
supplier branding, old title furniture, frame and explicit watermarks may be
nontechnical exclusions. An unknown object, unplaced technical object or ink
blocks candidate delivery (`SOURCE_INVENTORY_BLOCKED` /
`FAIL_SOURCE_COMPLETENESS`). Legacy diagnostic drafts remain isolated.

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

The semantic output slots in `canonical-output-layout.json` place model/Pin
tables at `RIGHT_TOP_TABLE`, performance at `RIGHT_MIDDLE_PERFORMANCE`, views
and PCB in `MAIN_ENGINEERING`, the fixed brand/title at bottom-right and the
approved English tolerance at the bottom. Source location never determines
destination slot or by itself creates `NEW_LAYOUT`. Ordinary technical ink is
Kangsheng blue; supplier/family-confirmed technical emphasis is low-saturation
gold regardless of whether the supplier used red, green or cyan. Ambiguous
color roles require `COLOR_SEMANTIC_REVIEW`, not a default-blue PASS. Source
technical values are never borrowed between products. A candidate may enter
family learning only with `SOURCE_INVENTORY_PASS`, color semantic PASS or
explicit approval, and `LAYOUT_QA_PASS`.

`scripts/supported_layouts.py` exposes `inspect_source()`, `load_catalog()`
and `preflight_source()`. Probe matches are diagnostic candidates, not
production permission. Unknown or ambiguous ink, additional technical notes,
new PCB/pin/tolerance structures and uncertain supplier branding remain
review-required. No automatic deletion occurs in preflight.
