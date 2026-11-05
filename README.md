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

The full comparison makes 72 runs, used about 1.5M tokens, and stops starting new runs at 2M tokens. It writes to `evals/results/`.

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

## Project layout

```
investigator/   Assignment core: agents, workflow, tools, evidence rules, baseline
evals/          Assignment comparison and scoring
data/           The 12 test incidents
reliability/    Extras: redaction, risky-action flag, safe run entry point
tests/core/     Tests for the core
tests/reliability/  Tests for the extras
cli.py          Investigate one incident
```

The core does not import the extras, and a test enforces this. `cli.py` and `evals` both use `reliability/`, so redaction and the risky-action flag are on in every reported result.

## Incomplete functionality and known limits

**Not built:**

- Regression gate against a saved baseline
- Tool for adding production findings to a golden dataset
- Per-agent cost and latency in the eval report (it is in the CLI report)
- Confidence calibration table
- Human approval step (risky actions are only flagged, and nothing is ever executed)

**Known limits:**

- The multi-agent system lost the comparison. Known problems are ties between hypotheses, and concluding the wrong cause with high confidence on INC-12.
- INC-07 fails in both systems on low confidence. The cause is not diagnosed.
- A Verifier label is an LLM judgement, so it can be wrong. Confidence labels are not calibrated.
- Rows are counted, not weighed. Repeated log lines can outweigh one decisive metric.
- A task that fails loses the evidence it had gathered.
- After a resume, the time limit restarts and the per-agent token split is incomplete.
- Tokens from failed model calls are not counted.
- Secret detection uses patterns, so it can miss other formats. The risky-action flag uses keywords.
- Only Gemini was run for real. Anthropic was only checked offline, and OpenAI was not tested.
- CI is defined in `.github/workflows/ci.yml` but has not run, because the repo has no remote.
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
