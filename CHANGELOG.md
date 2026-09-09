# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

- Added `HumanRenderer` and `HumanTraceSink` for bounded, readable projection
  and optional exact JSONL fanout of trace records.

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

[Unreleased]: https://github.com/icvoss/icv-trace/compare/v0.1.0rc2...HEAD
[0.1.0rc2]: https://github.com/icvoss/icv-trace/releases/tag/v0.1.0rc2
[0.1.0rc1]: https://github.com/icvoss/icv-trace/releases/tag/v0.1.0rc1
