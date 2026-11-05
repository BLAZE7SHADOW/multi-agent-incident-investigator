# Technical Note

## Key decisions

**LangGraph plus LangChain built-ins.** Parallelism, retries, timeouts, failure routing, checkpoints and redaction come from libraries. Custom code covers only task dependencies, a budget shared across agents, evidence rules, mock tools and scoring.

**LLMs judge, code decides.** At first, the LLM scored its own confidence. That number meant little, so we removed it. A Verifier now labels each row without seeing what was cited. Code then calculates confidence, ranking and the outcome.

**Deterministic scoring.** Evaluation compares category, component and outcome against known answers, with no LLM judge. Scores are simple to check and cannot be talked up.

**Simple mock data, local-first.** The data is easy to explain, and evals need only a model key. We skipped hosted tracing and TypeSafe because they add vendors without helping the required comparison.

**Same rules for both systems.** The baseline shares the model, tools, budget, evidence rules and report format. The only difference is one agent doing all the work.

## Results

The single agent won: 89% accuracy against 69%, and 89% against 33% on holdout incidents. It also used 25% fewer tokens and was 41% faster.

Multi-agent found slightly more key evidence, with 86% recall against 81%. But it made more unsupported claims and reached the only wrong conclusions. On INC-12, it twice blamed a deploy that had already been rolled back, with high confidence.

Our reading, not a proven cause, is that splitting the work created new problems. The Verifier often supports the same rows for several hypotheses, so rivals tie and the system refuses to conclude. The Critic adds caution, yet did not stop the INC-12 errors. On simple data, one agent with full context may have an advantage.

We kept these results as measured. Tuning until multi-agent won would make the comparison meaningless.

## Trade-offs

- **Caution against coverage.** Requiring two source types avoids some wrong answers but misses causes visible in one source.
- **Independence against cost.** The Verifier and Critic add calls and latency, and did not improve accuracy here.
- **Limits against thoroughness.** Our token fixes, including tighter limits, cut INC-08 from about 104k to 30k tokens, but may stop useful investigation.
- **Low thinking against quality.** Thinking was 74% of output tokens in one measured run. Reducing it may hurt hard cases.

## Limitations

The sample is small: 12 incidents, 3 holdout, on one model. We tuned on dev incidents after seeing results. The baseline's prompt used lessons from the multi-agent system, and it got more turns. Mock data is simpler than real telemetry. Calibration showed high confidence was never more reliable than medium.

## Potential improvements

1. Re-run the evaluation with the two new rules, which offline took the rule eval from 6/13 to 12/13.
2. Check how long before an incident a change happened.
3. Try one agent plus one independent checker.
4. Weigh evidence instead of counting rows.
5. Add new unseen incidents, since the holdout has been used.
6. Fill the golden dataset with reviewed production findings.
