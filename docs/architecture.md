# A2R2 Architecture

```text
Any Agent -> A2R2 Runtime -> Executor / Android Device
                  |
                  v
          Trace Recorder / Scorecard
```

A2R2 is a black-box runtime boundary around an Android GUI agent. The wrapped
agent can use rules, local models, cloud models, or human demonstrations. A2R2
does not care how the action was proposed.

The runtime only decides whether a proposed action is safe to execute now,
whether the executed action caused observable progress, and how to label and
record failures.

AndroidWorld tells whether an agent failed.
A2R2 tells why and when it started failing before it fully failed.

## v0.1 Components

- `ReliabilityRuntime`: public API for pre-action gates, post-action progress verification, and trace recording.
- `ReadinessPolicy`: deterministic XML/text readiness checks.
- `RiskPolicy`: generic high-risk action detection and handoff.
- `ProgressPolicy`: hash and artifact based progress verification.
- `TraceRecorder`: `trace.v1` JSONL writer and episode summary writer.
- `Scorecard`: trace reader that reports reliability metrics without inventing missing benchmark numbers.

## Non-Goals

A2R2 does not plan, replan, decompose goals, train VLMs, bypass login/captcha
walls, or replace AndroidWorld. Optional VLM evidence providers can be added in
future versions, but v0.1 has no VLM dependency.
