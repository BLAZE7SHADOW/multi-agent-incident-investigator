# Multi-Agent Incident Investigator

An AI system that investigates production incidents. It reads logs, metrics, deployments, service dependencies and past incidents. It then reports the likely root cause, the evidence for and against it, and what to do next. If the evidence is not enough, it says so.

The data is simulated and can be incomplete or misleading.

## Main result

We compared the multi-agent system with a single agent that has the same tools. **The single agent won.**

| Metric | Multi-agent | Single agent |
|---|---|---|
| Root-cause accuracy | 69% | 89% |
| Holdout accuracy (incidents not used for tuning) | 33% | 89% |
| Tokens per run | 24,303 | 18,266 |
| Mean latency | 45s | 27s |

See [Evaluation](#evaluation) for all metrics and [NOTE.md](NOTE.md) for why.

## Setup

You need [uv](https://docs.astral.sh/uv/) and an API key. uv installs Python 3.12 for you.

```bash
uv sync --all-extras
cp .env.example .env    # then add your API key to .env
```

Default settings in `.env`:

```
AI_PROVIDER=gemini
AI_MODEL=gemini-3.6-flash
GEMINI_API_KEY=your-key
AI_THINKING=low
```

To change provider, change these values. No code changes are needed. For example: `AI_PROVIDER=anthropic`, `AI_MODEL=claude-sonnet-5-5`, `ANTHROPIC_API_KEY=your-key`.

A free Gemini key may not be enough. One run made 3 to 23 model requests in our evaluation, and our free key hit a limit of 20 requests per day.

## Run

| Task | Command | Needs API key |
|---|---|---|
| Run all tests | `uv run pytest` | No |
| Investigate one incident | `uv run python cli.py INC-01` | Yes |
| Continue a crashed run | `uv run python cli.py INC-01 --resume <thread>` | Yes |
| Run the full comparison | `uv run python -m evals --repeats 3` | Yes |
| Run a small comparison | `uv run python -m evals --repeats 1 --cases INC-01 INC-08` | Yes |
| Calibration and cost report | `uv run python -m reliability.analysis` | No |
| Check for regressions | `uv run python -m reliability.regression` | No |
| Score the evidence rules alone | `uv run python -m reliability.rule_eval` | No |
| Recalculate every saved score | `uv run python -m reliability.audit` | No |
| Run metamorphic robustness tests | `uv run python -m reliability.metamorphic` | No |

The full comparison makes 72 runs, used about 1.5M tokens, and stops starting new runs at 2M tokens. It writes to `evals/results/`.

A shortened report from a real run:

```
Outcome: CONCLUSIVE (stopped: critic_accepted)

Hypotheses:
  1. [high] Deployment v2.4.0 of payments service introduced a bug in
     ChargeHandler.applyDiscount causing NullPointerExceptions. (bad_deploy, payments)
     supports: ['dep-01', 'log-02', 'log-03']  contradicts: []
     verified ratio: 1.00  net support: 3
  2. [low] Payvendor API failure caused payments service to crash. (dependency_failure, payvendor)
     supports: []  contradicts: []
     verified ratio: 0.00  net support: 0

Facts: 5  Inferences: 2
Assumptions: 3
Actions:
  - Roll back the payments service deployment v2.4.0. [needs approval]

Usage: 7 tool calls, 11 LLM calls, 15486 tokens (1297 thinking). Citation validity: 100%
```

## Architecture

```mermaid
flowchart TD
    A([Incident report]) --> B[Orchestrator<br/>plans tasks]
    B --> C{Scheduler<br/>which tasks are ready?}
    C -- run in parallel --> D[Observability agent<br/>logs, metrics]
    C -- run in parallel --> E[Dependency agent<br/>deployments, topology, history]
    D --> C
    E --> C
    C -- all tasks finished --> F[Reasoning agent<br/>competing hypotheses]
    F --> G[Verifier agent<br/>labels each row]
    G --> H[Evidence rules in code<br/>rank and score]
    H --> I[Critic agent<br/>challenges the top answer]
    I -- needs more evidence --> B
    I -- accepted, or a limit reached --> J[Outcome rules in code<br/>conclusive or inconclusive]
    J --> K[Reporting agent<br/>summary and actions]
    K --> L([Final report])
    S[(Shared state<br/>saved to SQLite)] -.- B
    S -.- C
    S -.- H
    S -.- J
```

### How the agents work together

| Agent | Job | Output |
|---|---|---|
| Orchestrator | Plans tasks and their order | `Plan` |
| Observability | Investigates logs and metrics | `Finding` |
| Dependency | Investigates deployments, config changes, topology and past incidents | `Finding` |
| Reasoning | Proposes competing root causes | `HypothesisSet` |
| Verifier | Labels each row for each hypothesis, without seeing what was cited | `Verification` |
| Critic | Challenges the top answer and asks for more evidence | `Critique` |
| Reporting | Writes the summary and actions | `ReportDraft` |

This is more than a chain of prompts:

- **Task order:** a task can wait for another one. Its agent then receives that task's findings.
- **Feedback:** the Critic sends typed requests. These become new tasks in a new round.
- **Shared evidence:** all agents use one evidence ledger. A row enters it only if a tool really returned it.
- **Independent check:** the Verifier does not see the citations, so it cannot simply agree with them.

**LLMs judge meaning. Code decides.** Code controls routing, limits, citation checks, confidence and the final outcome. The Reporting agent cannot change the conclusion.

### How a conclusion is reached

The LLM never scores itself. Code calculates these values from the Verifier's labels. Only logs, metrics and deployments count, because topology and past incidents do not show what happened in this incident.

| Value | Rule |
|---|---|
| Verified ratio | Cited rows labelled `supports` ÷ cited rows |
| Net support | Rows labelled `supports` − rows labelled `contradicts` |
| High confidence | Ratio ≥ 0.8, 2+ source types, no contradictions, no failed tools |
| Medium confidence | Ratio ≥ 0.5, 2+ source types, positive net support |
| Low confidence | Anything else |
| Conclusive | Critic accepts, at least medium confidence, and net support at least 1 above the runner-up |

Anything else is **inconclusive**, and the report says why. A fact with no valid citation becomes an assumption. Confidence is a rule-based label, not a probability.

### Reliability

| Failure or risk | What happens |
|---|---|
| Tool timeout or error | Retried up to 2 times, then recorded as an evidence gap |
| Partial tool results | Flagged, so the agent can narrow its query |
| Model error | Retried up to 3 times. A daily quota error stops the run at once and is reported |
| Task fails or hangs | Marked failed after a 120s limit. The investigation continues |
| Planner fails | A default plan is used |
| Reasoning, Verifier or Critic fails | The run ends inconclusive and still gives a report |
| Report writer fails | A template writes the report. The conclusion does not change |
| Process crashes | The run continues from its last SQLite checkpoint |

Limits for each run: 2 rounds, 25 tool calls, 80,000 tokens and 300 seconds. Limits for each task: 4 model turns and 5 tool calls. Each round has at most 4 tasks. A run also stops if the Critic accepts, says the evidence is insufficient, requests nothing, or a new round finds no new evidence.

## Dataset

[`data/incidents.yaml`](data/incidents.yaml) has 12 incidents in a fictional online shop. Tools see only the telemetry. The correct answers are stored apart, and a test proves tools cannot return them.

| Incident | Scenario | Tests |
|---|---|---|
| INC-01 | Bad deploy | Control case |
| INC-02 | Config change in a dependency | Cause far from symptom |
| INC-03 | Harmless deploy near the real cause | Misleading evidence |
| INC-04 | Past incident looks the same | Misleading evidence |
| INC-05 | Logs show errors, default metrics look healthy | Conflicting observations |
| INC-06 | Loud old errors, quiet real signal | Misleading evidence (holdout) |
| INC-07 | Log tool times out twice, then recovers | Tool failure |
| INC-08 | Deployment tool down, logs partial | Tool failure, expected inconclusive |
| INC-09 | Slow auth causes a retry storm | Misleading evidence (holdout) |
| INC-10 | Symptoms only | Expected inconclusive |
| INC-11 | Log contains a prompt injection and a fake secret | Security |
| INC-12 | Deploy rolled back, errors continue; metrics partial | Conflicting observations, tool failure (holdout) |

## Evaluation

Both systems used the same model, tools, budget, evidence rules and report format. Each ran every incident 3 times, for 72 valid runs on `gemini-3.6-flash`. Scoring is plain code, with no LLM judge.

| Metric | Multi-agent | Single agent |
|---|---|---|
| Root-cause accuracy | 69% | 89% |
| Wrong conclusions | 6% | 0% |
| Missed conclusions | 25% | 11% |
| Fell for misleading evidence | 6% | 0% |
| Citation validity | 100% | 100% |
| Key evidence recall | 86% | 81% |
| Key evidence precision | 69% | 83% |
| Claims downgraded per run | 1.3 | 0.1 |
| Tool calls per run | 11.2 | 12.1 |
| LLM calls per run | 15.0 | 5.3 |
| Tokens per run | 24,303 | 18,266 |
| Mean latency | 45s | 27s |

Correct runs out of 3:

| Incident | Multi-agent | Single agent |
|---|---|---|
| INC-01 to INC-06, INC-08, INC-10 | 3/3 each | 3/3 each |
| INC-07 | 0/3 | 0/3 |
| INC-09 (holdout) | 0/3 | 2/3 |
| INC-11 | 1/3 | 3/3 |
| INC-12 (holdout) | 0/3 | 3/3 |

**How to read these numbers:**

- A correct run needs the right outcome. If the case is conclusive, it also needs the right category and component.
- **Tuning was on dev incidents only.** We changed prompts and one evidence rule after seeing dev results. We made no changes after the holdout incidents first ran.
- **The sample is small.** There are 12 incidents and 3 in the holdout, so treat differences as a signal, not proof.
- **The comparison may favour the single agent.** It got 12 turns against 4 per task, and its prompt used lessons learned from the multi-agent system.
- The prompt injection did not work: no INC-11 run concluded `gateway`, the answer the log line pushes for.

Raw data: [`results.md`](evals/results/results.md) and [`results.json`](evals/results/results.json).

## Requirements map

| Requirement | Where | Proof |
|---|---|---|
| At least 5 distinct agents | `investigator/agents.py` | `tests/core/test_agents.py` |
| Structured results, real collaboration | `investigator/schemas.py`, `investigator/graph.py` | `tests/core/test_agents.py` |
| Tools with structured input and output | `investigator/tools.py` | `tests/core/test_tools.py` |
| Error handling, timeouts, incomplete results | `investigator/tools.py` | `tests/core/test_tools.py` |
| Parallel tasks, task dependencies, shared state | `investigator/graph.py` | `tests/core/test_graph.py` |
| Feedback loops | `investigator/graph.py` | `tests/core/test_graph.py` |
| Bounded retries, limits, termination | `investigator/budget.py`, `investigator/llm.py` | `tests/core/test_graph.py` |
| Recovery from agent and tool failures | `investigator/graph.py` | `tests/core/test_graph.py` |
| Ranked hypotheses, evidence for and against, confidence | `investigator/evidence.py` | `tests/core/test_evidence.py` |
| Recommended actions | `investigator/graph.py`, `investigator/evidence.py` | `tests/core/test_evidence.py` |
| Facts vs assumptions, inconclusive outcome | `investigator/evidence.py` | `tests/core/test_evidence.py` |
| 10+ test incidents | `data/incidents.yaml` | `tests/core/test_tools.py` |
| Single-agent baseline, same tools | `investigator/baseline.py` | `tests/core/test_baseline.py` |
| Accuracy, grounding, tool calls, latency, tokens | `evals/scoring.py` | `tests/core/test_evals.py` |
| No hardcoded answers | Tools never receive answers | `tests/core/test_tools.py` |
| Technical note, 500 words or fewer | [NOTE.md](NOTE.md) | |
| Coding assistant transcript | [TRANSCRIPT.md](TRANSCRIPT.md) | |

## A fix we tried and removed

The evaluation showed multi-agent usually found the right cause, then our rules blocked it. We tried two rules:

1. A deploy or config cause needs a matching deployment record.
2. A cause is ruled out if errors continued after its rollback.

On the 13 cases they were designed from, correct results rose from 6 to 12. On 8 new real-life cases, they fell from 6 to 3. The rules matched exact names, such as `payments` and `kind: deploy`, and treated missing records as proof. Real systems use other names, feature flags, regional rollbacks and several deploys at once. **We removed both rules**, because they fit our test cases but not real incidents. They remain in git history, in commit `696df01`.

### Evidence rule evaluation

`reliability.rule_eval` checks the evidence rules alone, with no model calls. Labels are what a perfect Verifier would give.

| Set | What it is | Correct |
|---|---|---|
| Reconstructed | Failed evaluation runs, rebuilt from saved summaries | 0/5 |
| Control | Cases from the test incidents with a clear answer | 6/8 |
| Adversarial | Real-life variations, frozen so no rule can be tuned on them | 6/8 |

All 21 scenarios together: precision 62%, recall 67%, 6 wrong conclusions. Full detail: [`rule_eval.md`](evals/results/rule_eval.md).

The developer wrote the expected answers, so treat these as evidence, not proof.

### Metamorphic tests

`reliability.metamorphic` changes each incident in a way that should not change the answer, then checks the answer did not change. It runs the real tools and evidence rules, with labels from each case's key evidence. No model calls are made, and nobody picks the expected answers: they come from the unchanged incident.

| Change | Should | Passed |
|---|---|---|
| Rename every service | Not change the answer | 10/10 |
| Rename metrics, such as `error_rate` to `http_5xx_ratio` | Not change the answer | 10/10 |
| Rename change kinds, such as `deploy` to `release` | Not change the answer | 10/10 |
| Deploy tool uses other service names | Not change the answer | 10/10 |
| Rename row IDs | Not change the answer | 10/10 |
| Shift all times by 400 days | Not change the answer | 10/10 |
| Shuffle row order | Not change the answer | 10/10 |
| Add 3 harmless log rows | Not change the answer | 10/10 |
| Add 25 harmless log rows | Not change the answer | **6/10** |
| Drop a key evidence row | Not raise confidence | 10/10 |
| Add a contradicting row | Not raise confidence | 10/10 |
| **Total** | | **106/110** |

**The suite catches overfitting.** Run against the removed rules, it scores 100/110. Those rules fail when change kinds are renamed, or when the deploy tool names services differently.

**It found one real weakness.** The log tool returns the oldest rows first and stops at 20. With 25 harmless older rows, the key evidence is cut off, and 4 incidents lose their right answer. Real log tools usually return the newest rows first. This is not fixed yet.

Limits: 10 incidents, oracle labels instead of a real Verifier, and one hypothesis per case, so ties are not tested here. Full detail: [`metamorphic.md`](evals/results/metamorphic.md).

### How the calculations are checked

| Check | Result |
|---|---|
| Hand-worked examples, with the working in comments | All pass |
| Property tests: thousands of generated inputs per rule | No counterexample found |
| Evidence precision and recall vs scikit-learn | 0 mismatches in 5,000 random cases |
| Wilson intervals vs SciPy | 0 mismatches in 860 cases |
| Audit: recalculate all 72 saved scores and the report | All match |

The audit cannot recheck evidence precision and recall in the saved runs, because they did not store cited rows. New runs store them, along with each row's Verifier label.

## Reliability extras

These go beyond the assignment. They add no agent behaviour and make no model calls, so the reported results stand. To control evaluation cost, the full evaluation was not re-run after adding them.

### Calibration

`reliability.analysis` reads saved results and reports how often each confidence label was right. Results are in [`analysis.md`](evals/results/analysis.md).

| System | Confidence | Correct | 95% range |
|---|---|---|---|
| Multi-agent | High | 9/11 (82%) | 52% to 95% |
| Multi-agent | Medium | 10/10 (100%) | 72% to 100% |
| Single agent | High | 22/22 (100%) | 85% to 100% |
| Single agent | Medium | 4/4 (100%) | 51% to 100% |

The labels did not prove useful here: high was never more reliable than medium. The ranges overlap, so the sample is too small to be sure.

To estimate cost, add your provider's current prices per million tokens: `--input-price 0.10 --output-price 0.40`. These example numbers are not real prices.

### Regression gate

Use this after any model, prompt or code change:

```bash
uv run python -m evals --repeats 3             # makes paid model calls
uv run python -m reliability.regression        # compares results, no model calls
uv run python -m reliability.regression --update   # only if you accept the new results
```

It exits with an error if any of these happen:

| Check | Limit |
|---|---|
| Accuracy | Falls by more than 5 points |
| Wrong conclusions, misleading evidence | Any rise |
| Citation validity | Falls by more than 2 points |
| Key evidence recall | Falls by more than 5 points |
| Tokens per run | Rises by more than 20% |
| Mean latency | Rises by more than 30% |
| Runs | Any invalid run, or a missing system or incident |

It also warns when any single incident gets worse. The limits are wide because repeated LLM runs vary. They are starting values, not tuned ones. The saved baseline is [`baseline.json`](evals/results/baseline.json).

### Golden dataset

Real findings can become test cases. A person must confirm each answer, so a system mistake cannot become the expected result.

```bash
uv run python -m reliability.golden draft runs/<incident>.json
# Edit the draft: redact it, then fill in truth, scenario and reviewed_by
uv run python -m reliability.golden add data/golden_drafts/<incident>.yaml
uv run python -m evals --golden --repeats 3    # makes paid model calls
```

A case is refused if no reviewer is named, if secrets or email addresses remain, if its ID already exists, or if its answer is incomplete. Reviewed cases go in [`data/golden_extra.yaml`](data/golden_extra.yaml), which is empty for now. The 12 assignment incidents stay frozen, so the reported results remain comparable. Use `--golden` only with a baseline created from a golden run.

## Continuous integration

GitHub Actions runs lint, format checks and all tests on every push. See `.github/workflows/ci.yml`. It uses fake models, so it needs no API key and makes no model calls.

## Project layout

```
investigator/   Assignment core: agents, workflow, tools, evidence rules, baseline
evals/          Assignment comparison and scoring
data/           The 12 test incidents, and reviewed golden cases
reliability/    Extras: safety, calibration, regression gate, golden dataset
tests/core/     Tests for the core
tests/reliability/  Tests for the extras
cli.py          Investigate one incident
```

The core does not import the extras, and a test enforces this. `cli.py` and `evals` both use `reliability/`, so redaction and the risky-action flag are on in every reported result.

## Incomplete functionality and known limits

**Not built:**

- Human approval step. Risky actions are only flagged, and nothing is ever executed, so there is nothing to approve yet.
- Hosted tracing, such as LangSmith. Everything runs locally.

**Built and tested offline, but not yet used on a new model run:**

- Per-agent tokens in the eval results. The current results do not have them. The CLI report already shows them.
- The `--golden` evaluation option. It is tested only as far as loading cases, and no golden case exists yet.
- The fixes listed in [NOTE.md](NOTE.md). They would change agent behaviour, so they need a new paid evaluation and new unseen incidents.

**Known limits:**

- The multi-agent system lost the comparison. Known problems are ties between hypotheses, and concluding the wrong cause with high confidence on INC-12.
- INC-07 fails in both systems on low confidence. The cause is not diagnosed.
- A Verifier label is an LLM judgement, so it can be wrong. Confidence labels did not separate right from wrong answers in this evaluation.
- Regression limits and secret checks catch only what they look for. Passing them does not prove a case or a change is safe.
- Summaries and action text are not checked against evidence. In one real run, an action named a version number that is not in the data. Hypotheses, ranking and the outcome are checked.
- Rows are counted, not weighed. Repeated log lines can outweigh one decisive metric.
- A task that fails loses the evidence it had gathered.
- After a resume, the time limit restarts and the per-agent token split is incomplete.
- Tokens from failed model calls are not counted.
- Secret detection uses patterns, so it can miss other formats. The risky-action flag uses keywords.
- Only Gemini was run for real. Anthropic was only checked offline, and OpenAI was not tested.
- The mock data is much simpler than real telemetry.

## External dependencies

| Dependency | Use | Required |
|---|---|---|
| LLM provider API (Gemini by default) | All agents | Yes, for real runs |
| LangGraph | Workflow, parallelism, retries, checkpoints | Yes |
| LangChain | Agents, provider switching, middleware | Yes |
| SQLite, through `langgraph-checkpoint-sqlite` | Crash recovery | Yes |
| Pydantic, PyYAML, python-dotenv | Data contracts, dataset, settings | Yes |

Incident text and tool output are sent to the LLM provider after redaction. Check your provider's data terms before using real logs. Free tiers may allow providers to use your data.
