# Reliability analysis

Calculated from 72 saved runs, with no new model calls.

## Calibration

Conclusions at each confidence label, and how many were correct. The range is a 95% Wilson interval. Wide ranges mean the sample is too small to be sure.

| System | Confidence | Correct | Rate | 95% range |
|---|---|---|---|---|
| multi_agent | high | 9/11 | 82% | 52% to 95% |
| multi_agent | medium | 10/10 | 100% | 72% to 100% |
| multi_agent | low | 0/0 | n/a | n/a |
| baseline | high | 22/22 | 100% | 85% to 100% |
| baseline | medium | 4/4 | 100% | 51% to 100% |
| baseline | low | 0/0 | n/a | n/a |

- **multi_agent:** High-confidence conclusions were right less often than medium.
- **baseline:** Both labels were right equally often, so these results cannot separate them.

Low-confidence hypotheses never conclude, by rule, so they do not appear as conclusions.

## Tokens by system

| System | Input per run | Output per run | Thinking |
|---|---|---|---|
| multi_agent | 18,285 | 6,019 | 3,047 |
| baseline | 15,134 | 3,131 | 1,374 |

## Tokens by agent

Not in these results. The evaluation now records it, so it appears from the next run.
