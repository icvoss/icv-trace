# Extraction provenance

The package was extracted from site commit
`087479b96929a56447c2ac8011011023b4df2dcd`, which includes the initial trial
source, the local module-name substitution and the bounded callback-capture
extension. The archived source remains under
`docs/reviews/trace-adapter-trial-2026-09-08/` in the ICV OSS umbrella.

| Archived file | SHA-256 |
| --- | --- |
| `runtime/src/trace_trial/core.py` | `c28fbce7140fb5a1e1b35b82d0bb180ca2c0407969ca781f8a023d8f494638b5` |
| `runtime/src/trace_trial/adapters.py` | `8734ed435e3c2de4d5053f4ffe6aacc41bbbd72f5cb2fe52ccfaffbb9fd8a489` |
| `checks/conftest.py` | `34989ba7b5e7d738d79746a12e448d004305c13c8f24e299bc238eef38107c1b` |
| `checks/test_admission_precision.py` | `2c5d785fe76ad8d53a871c8db1c01cda2e9006d0c57c967aa62004b2ce10bba3` |
| `checks/test_core_hardening.py` | `6c6c6f1662d69184613733590a23ca1ac9896d2d6180d2c776fdd7fb450f395d` |
| `checks/test_invalid_child_admission.py` | `7d156e1dd344b1e1ca62916329b049df02ed49a0572294b00e1247629b29623b` |
| `checks/test_trace_contract.py` | `f929a0d2b31e8a3ab7e451c66cba40a535fa1d7cc0ac4fe18aba3abcecf0bd34` |
| `checks/test_trace_edges.py` | `7e359d32d57afc4d7f0cd3a258f5944f830a33c8b36fc13c55283a4b464547f2` |

The extracted files are not byte-identical to the archive. The module import
was substituted from `trace_trial` to `icv_trace`, and the callback bridge was
added during incubation. Extraction adds packaging metadata, public module
docstrings and `__version__`, and replaces the former `ICV_TRACE_SRC`
source-mode fixture with a built-wheel origin guard. It also adds
non-behavioural annotations and casts required by the blocking mypy gate, and
removes one unused test import. The archived hashes above remain provenance for
the source history; they are not hashes of the extracted files.

The rejected comparison-adapter delivery selector is not a supported API.
`TextIOSink` and `textio_sink` are the only supported delivery helpers; the
core API remains public. Logging, Eliot and OpenTelemetry helpers remain
experimental comparison code, with no delivery or conformance promise.
