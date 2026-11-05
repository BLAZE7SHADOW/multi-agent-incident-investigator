# Evaluation results

Model: `gemini:gemini-3.6-flash`

| Metric | multi_agent | baseline |
|---|---|---|
| Valid runs | 24 | 24 |
| Invalid runs (excluded) | 0 | 0 |
| Root-cause accuracy | 75% | 71% |
| Wrong conclusions | 8% | 21% |
| Missed conclusions | 17% | 8% |
| Fell for misleading evidence | 0% | 0% |
| Citation validity | 100% | 100% |
| Key evidence recall | 68% | 74% |
| Key evidence precision | 41% | 48% |
| Claims downgraded per run | 1.2 | 0.0 |
| Tool calls per run | 11.4 | 12.5 |
| LLM calls per run | 15.4 | 5.7 |
| Tokens per run | 29,403 | 23,672 |
| Thinking tokens per run | 2,724 | 1,878 |
| Mean latency (s) | 38 | 26 |
| Median latency (s) | 31 | 25 |

## Accuracy by split

Prompts and evidence rules were tuned on dev incidents. Holdout incidents were not.

| Split | multi_agent | baseline |
|---|---|---|
| holdout | 75% | 71% |

## By incident (correct runs out of valid runs)

| Incident | Scenario | multi_agent | baseline |
|---|---|---|---|
| BLIND-01 | A payments release with a new FX rounding policy throws NullPointerException on every transfer; straightforward deploy regression. | 1/1 | 1/1 |
| BLIND-02 | A fraud-scoring rules config lowered the block threshold right after an unrelated payments deploy; the deploy is the closer-looking suspect. | 1/1 | 1/1 |
| BLIND-03 | Ledger WAL disk filled up over hours because archiving silently failed; a harmless ledger deploy minutes before the crash and a past bad-migration incident point at a deploy. | 1/1 | 1/1 |
| BLIND-04 | The card network vendor returned 503s; a cards deploy ten minutes earlier worked fine in between and is a timing red herring. | 1/1 | 1/1 |
| BLIND-05 | The ledger's internal serving certificate expired at midnight; warning was logged the day before. | 0/1 | 0/1 |
| BLIND-06 | Payday midnight: salary credits and pushes drive a 6x request spike; gateway sheds load. No recent deploy despite a history entry about a gateway deploy. | 1/1 | 1/1 |
| BLIND-07 | A ledger-core release renamed a column before its migration ran; four changes landed within 15 minutes and the ledger is named differently across tools. | 0/1 | 0/1 |
| BLIND-08 | A device-binding feature flag was ramped to 100% under a different service name, rejecting older app versions; accounts latency from a batch job is a concurrent distraction. | 0/1 | 0/1 |
| BLIND-09 | Fraud-scoring's unbounded merchant cache exhausts JVM heap after a large partner onboarding and pods OOM in a loop; payments shows the symptoms. | 1/1 | 1/1 |
| BLIND-10 | The identity-verification vendor's EU endpoint is timing out; kyc and accounts only show downstream symptoms and US is healthy. | 1/1 | 1/1 |
| BLIND-11 | Kestrel's own mTLS client certificate for the card network expired after auto-renew was disabled; history of vendor outages and differently named cards service mislead. | 0/1 | 0/1 |
| BLIND-12 | A tax-statement campaign sent 2.1M users to download PDFs; the statements surge overloads the shared ledger replica so ledger looks like the culprit. | 1/1 | 1/1 |
| BLIND-13 | A cards canary release in eu-west broke EMV field encoding; errors drain slowly after the regional rollback and past vendor outages tempt a cardnet diagnosis. | 1/1 | 1/1 |
| BLIND-14 | A typo in an SMS rate-limit config throttled OTP delivery so codes expired before arrival; auth shows the errors and the queue drains slowly after revert. | 0/1 | 0/1 |
| BLIND-15 | Accounts in us-east runs out of file descriptors; kyc vendor latency is elevated globally at the same time but EU accounts is fine, and accounts appears under several names. | 1/1 | 1/1 |
| BLIND-16 | Card network latency jumped 15x, tying up cards' worker pool; pool saturation looks like resource exhaustion in cards but CPU is low and the vendor posted a notice. | 1/1 | 1/1 |
| BLIND-17 | The ap-south edge certificate expired after renewals failed for days; a gateway deploy minutes earlier went to all regions yet only ap-south broke. | 0/1 | 1/1 |
| BLIND-18 | A credential-stuffing burst from two ASNs saturated auth in us-east; earlier auth deploy and config change are unrelated and latency recovers slowly after the WAF block. | 1/1 | 1/1 |
| BLIND-19 | A 12-minute self-resolving timeout burst to every backend with no backend errors, no changes and flat dependencies; history suggests causes the data cannot confirm. | 1/1 | 0/1 |
| BLIND-20 | Logs and deployment tools are down; metrics only show payments errors with mild movement in several dependencies, so no cause can be singled out. | 1/1 | 1/1 |
| BLIND-21 | A cards client rewrite and a vendor degradation notice coincide exactly; the only evidence is timeouts to the vendor, which both explain equally. | 1/1 | 1/1 |
| BLIND-22 | Statements' logs for the window were dropped by the log shipper, metrics come back truncated, and nothing else changed recently; the cause is not in the data. | 1/1 | 0/1 |
| BLIND-23 | EU payments latency rose with flat CPU, flat dependencies and no errors; the only nearby change was scoped to us-east, so nothing in the data explains it. | 1/1 | 1/1 |
| BLIND-24 | Three services hit connection resets to unrelated targets at the same instant with no changes and healthy targets; a shared network or infra cause is likely but nothing identifies it. | 1/1 | 1/1 |
