# AgentPack E2E A/B Status

No public AgentPack vs no-AgentPack E2E outcome report is published yet.

Current public benchmark evidence is file-selection evidence only:

- current table: `benchmarks/results/2026-07-06-public.md`
- cases: 107 public commit checks
- recall: 67.2%
- token precision: 50.6%

Do not claim task-success, tool-call, cost, or wall-time improvement until a
dated `benchmarks/results/*-e2e-ab.md` report exists and compares the same cases,
agent command, validation commands, and trial counts across baseline and
AgentPack strategies.
