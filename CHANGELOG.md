# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

## [0.1.0rc5] - 2026-09-11

### Fixed

- Strengthened AC-TRACE-004 and AC-TRACE-012 acceptance coverage: oversized
  strings are rejected in the public scalar suite, and business stdout stays
  byte-identical with detail off and on while structured output uses a separate
  sink.
- Published install guidance matches the completed public-index closeout. The
  previous PyPI long description for `0.1.0rc4` still mentioned the private
  index for rc3 after those artefacts were archived.

### Consumer behaviour

- Runtime API and record semantics are unchanged from `0.1.0rc4`. Existing
  consumers may stay on rc4; upgrade to rc5 for the corrected PyPI description
  and the stronger acceptance suite only.

## [0.1.0rc4] - 2026-09-09

### Changed

- Prepared the CI and tag workflow for public PyPI publishing through a PyPI
  trusted publisher on GitHub-hosted runners. The release gate now also checks
  that `human.py` is included in the wheel.
- Added self-contained public contracts and renderer guides, contributor and
  release instructions, and source-distribution copies of docs and examples.

### Consumer behaviour

- Runtime behaviour and the supported package-root API are unchanged from
  `0.1.0rc3`. This release moves the distribution target to PyPI. Private-index
  consumers should validate an explicit `0.1.0rc4` PyPI install before removing
  their private extra index, and retain that index while other private packages
  still need it.

## [0.1.0rc3] - 2026-09-09

### Added

- Added `HumanRenderer` and `HumanTraceSink` for bounded, readable projection
  and optional exact JSONL fanout of trace records.

### Consumer behaviour

- Existing trace bindings, canonical human output, structured records and
  admission budgets retain their behaviour. Readable rendering is opt-in:
  hosts bind `HumanTraceSink` as the structured destination and inspect its
  output health separately from core health. Its human view omits correlation
  IDs; optional JSONL fanout preserves the original records. The core charges
  structured bytes, not downstream presentation/fanout bytes.

## [0.1.0rc2] - 2026-09-09

### Fixed

- Added explicit Python setup before the private publish workflow validates
  release metadata. `v0.1.0rc1` stopped in that pre-upload job because the
  bare self-hosted runner had no `python` command; no distribution was
  uploaded and the trace API is unchanged.

## [0.1.0rc1] - 2026-09-09

### Unpublished

- `v0.1.0rc1` did not upload a distribution. It remains historical evidence
  of the failed pre-upload workflow run and must not be retagged.

### Added

- Extracted the pure-Python trace core from icvlocal at source commit
  `087479b96929a56447c2ac8011011023b4df2dcd`.
- Added bounded lifecycle diagnostics, correlation context, sink health and
  same-thread callback capture as the initial private release candidate.

### Changed

- Existing incubated consumers keep the same public API and runtime behaviour.
  The implementation is now distributed as `icv-trace` for index installation.

### Release gate record

- The release owner completes and records all six private release gates on the
  exact tagged commit before pushing `v0.1.0rc2`: version and changelog,
  green CI, clean publish-workflow simulation, artefact inspection and clean
  installation, tagged-workflow review, and consumer-facing behaviour notes.

[Unreleased]: https://github.com/icvoss/icv-trace/compare/v0.1.0rc5...HEAD
[0.1.0rc5]: https://github.com/icvoss/icv-trace/releases/tag/v0.1.0rc5
[0.1.0rc4]: https://github.com/icvoss/icv-trace/releases/tag/v0.1.0rc4
[0.1.0rc3]: https://github.com/icvoss/icv-trace/releases/tag/v0.1.0rc3
[0.1.0rc2]: https://github.com/icvoss/icv-trace/releases/tag/v0.1.0rc2
[0.1.0rc1]: https://github.com/icvoss/icv-trace/releases/tag/v0.1.0rc1
