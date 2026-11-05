# Technical Note

## Key decisions

**LangGraph plus LangChain built-ins.** Parallelism, retries, timeouts, failure routing, checkpoints and redaction come from libraries. Custom code covers only task dependencies, a shared budget, evidence rules, mock tools and scoring.

**LLMs judge, code decides.** At first, the LLM scored its own confidence. That number meant little, so we removed it. A Verifier now labels each row without seeing what was cited, and code calculates confidence, ranking and the outcome.

**Deterministic scoring.** Evaluation compares category, component and outcome against known answers, with no LLM judge.

**Same rules for both systems.** The baseline shares the model, tools, budget, evidence rules and report format. The only difference is one agent doing all the work.

**Tests we cannot tune for.** A separate agent wrote 24 blind incidents without seeing the system. Metamorphic tests check that renames, noise and reordering do not change answers.

## Results

**v1, 12 assignment incidents:** the single agent won, 89% against 69%, and 89% against 33% on holdout. Multi-agent often found the right cause, then its rules blocked it. It also blamed a rolled-back deploy twice.

**v2, 24 blind incidents:** we added tie-breaking requests and shared findings. Accuracy was a tie, 75% against 71% (p = 1.0). Multi-agent made fewer wrong conclusions, 2 against 5, but missed more real causes and cost 24% more tokens.

Our reading, not a proven cause: on simple incidents, one agent with full context does as well, and extra agents add caution rather than accuracy.

Two rules we wrote from known failures raised those cases from 6/13 to 12/13, but realistic ones fell from 6/8 to 3/8. We removed them.

## Trade-offs

- **Caution against coverage:** requiring two source types avoids some wrong answers but misses causes visible in one source.
- **Independence against cost:** the Verifier and Critic add calls and latency. They improved safety, not accuracy.
- **Limits against thoroughness:** tighter limits cut INC-08 from about 104k to 30k tokens, but may stop useful investigation.
- **Low thinking against quality:** thinking was 74% of output tokens in one run. Reducing it may hurt hard cases.

## Limitations

Samples are small, and the blind set ran once per incident on one model. The v1 and v2 results use different incidents, so the effect of the v2 changes is not measured. Tools match service names exactly. Calibration showed high confidence was never more reliable than medium. Mock data is simpler than real telemetry.

## Potential improvements

1. Evaluate each agent as a funnel, to find where answers are lost.
2. Add a service catalog, so differently named services match.
3. Weigh evidence instead of counting rows.
4. Run v1 on the blind set, to measure the v2 changes.
5. Add blind incidents written by a person.
6. Fill the golden dataset with reviewed production findings.
