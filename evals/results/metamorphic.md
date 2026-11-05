# Metamorphic test results

Unchanged cases solved: 10/10.

10 incidents with a known cause, each changed 13 ways, for 130 checks. Runs the real tools and evidence rules, with labels from each case's key evidence. No model calls.

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
| add 25 noise rows | same | 10/10 |
| add 25 noise rows after the report | same | 10/10 |
| add 25 harmless warnings | same | 10/10 |
| drop a key evidence row | not_more | 10/10 |
| add a contradicting row | not_more | 10/10 |
| **Total** | | **130/130** |

## Failures

None.
