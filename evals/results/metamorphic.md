# Metamorphic test results

10 incidents with a known cause, each changed 11 ways, for 110 checks. Runs the real tools and evidence rules, with labels from each case's key evidence. No model calls.

| Change | Relation | Passed |
|---|---|---|
| rename services | same | 10/10 |
| rename metrics | same | 10/10 |
| rename change kinds | same | 10/10 |
| deploy tool uses other service names | same | 10/10 |
| rename row IDs | same | 10/10 |
| shift time by 400 days | same | 10/10 |
| shuffle row order | same | 10/10 |
| add 3 noise rows | same | 10/10 |
| add 25 noise rows | same | 6/10 |
| drop a key evidence row | not_more | 10/10 |
| add a contradicting row | not_more | 10/10 |
| **Total** | | **106/110** |

## Failures

| Incident | Change | What changed |
|---|---|---|
| INC-04 | add 25 noise rows | outcome: conclusive -> inconclusive; confidence: high -> low |
| INC-06 | add 25 noise rows | outcome: conclusive -> inconclusive; confidence: high -> low |
| INC-07 | add 25 noise rows | outcome: conclusive -> inconclusive; confidence: high -> low |
| INC-11 | add 25 noise rows | outcome: conclusive -> inconclusive; confidence: high -> low |
