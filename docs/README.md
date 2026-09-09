# icv-trace documentation

Start with the package [README](../README.md) for installation and a small
operation. These documents describe the stable v1 boundary in consumer terms.

- [Contracts](contracts.md) defines accepted operation facts, policy and
  limits, records, output health, context, supervision and compatibility.
- [Human renderer](human-renderer.md) shows the readable projection for
  services, commands, tasks and pipelines.
- [`examples/human_trace.py`](../examples/human_trace.py) is a runnable,
  dependency-free command/pipeline/service example, including a simulated
  supervisor observation.

The documents are public package copies of the current contract source. Each
records its source revision and must be synchronised with it until the public
graduation change makes these documents normative.
