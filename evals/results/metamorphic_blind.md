# Metamorphic test results

Unchanged cases solved: 16/18 (not solved: BLIND-11, BLIND-15).

18 incidents with a known cause, each changed 13 ways, for 234 checks. Runs the real tools and evidence rules, with labels from each case's key evidence. No model calls.

| Change | Relation | Passed |
|---|---|---|
| rename services | same | 18/18 |
| rename metrics | same | 18/18 |
| rename change kinds | same | 18/18 |
| deploy tool uses other service names | same | 18/18 |
| rename row IDs | same | 18/18 |
| shift time by 400 days | same | 18/18 |
| shuffle row order | same | 18/18 |
| add 3 noise rows | same | 18/18 |
| add 25 noise rows | same | 18/18 |
| add 25 noise rows after the report | same | 18/18 |
| add 25 harmless warnings | same | 18/18 |
| drop a key evidence row | not_more | 18/18 |
| add a contradicting row | not_more | 18/18 |
| **Total** | | **234/234** |

## Failures

None.
