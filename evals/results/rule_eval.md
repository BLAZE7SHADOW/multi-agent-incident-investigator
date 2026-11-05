# Evidence rule evaluation

Scores the evidence rules alone on scenarios from `data/rule_eval.yaml`, with no model calls. Labels are what a perfect Verifier would give, so this shows what the rules decide, not what the full system would do with a real model. Expected answers were written by the developer.

## Correct by set

| Set | Scenarios | Result |
|---|---|---|
| reconstructed | 5 | 0/5 |
| control | 8 | 6/8 |
| adversarial | 8 | 6/8 |

## All scenarios

| Metric | Result |
|---|---|
| Scenarios correct | 57% |
| Precision | 62% |
| Recall | 67% |
| Wrong conclusions | 6 |
| True positives (right cause concluded) | 10 |
| False positives (wrong conclusion) | 6 |
| False negatives (right cause missed) | 5 |
| True negatives (rightly inconclusive) | 2 |

Precision = TP / (TP + FP). Recall = TP / (TP + FN). A wrong conclusion counts as both a false positive and a false negative, because it also misses the right cause.

## Scenarios

| Scenario | Set | Result |
|---|---|---|
| INC-11, unevidenced config rival ties the real cause | reconstructed | ✗ inconclusive, resource_exhaustion/inventory, net [2, 2] |
| INC-11, "unknown" rival ties the real cause | reconstructed | ✗ inconclusive, resource_exhaustion/inventory, net [2, 2] |
| INC-09, unevidenced config rival with 6 rows ties the real cause | reconstructed | ✗ inconclusive, resource_exhaustion/auth, net [6, 6] |
| INC-12, rolled-back deploy beats the real cause by 1 | reconstructed | ✗ conclusive, bad_deploy/payments, net [4, 3] |
| INC-12, rolled-back deploy beats the real cause by 1, smaller | reconstructed | ✗ conclusive, bad_deploy/payments, net [3, 2] |
| INC-01 control, real deploy cause must still conclude | control | ✓ conclusive, bad_deploy/payments, net [3, 0] |
| INC-02 control, real config cause must still conclude | control | ✓ conclusive, config_change/inventory, net [3, 0] |
| INC-05 control, regional config cause must still conclude | control | ✓ conclusive, config_change/gateway, net [3, 0] |
| INC-03 control, harmless deploy must not win | control | ✓ conclusive, resource_exhaustion/redis, net [3, 1] |
| INC-12 control, a rollback that fixed errors must not block a real deploy cause | control | ✓ conclusive, bad_deploy/payments, net [3, 1] |
| INC-08 control, deploy blamed with no deployment data must not conclude | control | ✗ conclusive, bad_deploy/payments, net [2] |
| INC-10, a 3-day-old deploy is blamed for a brief blip | control | ✗ conclusive, bad_deploy/checkout, net [2] |
| INC-10 control, the critic's "insufficient" must hold | control | ✓ inconclusive, bad_deploy/checkout, net [2] |
| Deploy logged under another name (payments-api) | adversarial | ✓ conclusive, bad_deploy/payments, net [2] |
| Change was a feature flag | adversarial | ✓ conclusive, config_change/payments, net [2] |
| Deploy blamed while the deploy tool was down | adversarial | ✗ conclusive, bad_deploy/payments, net [2] |
| Rollback worked, but errors took 30 minutes to drain | adversarial | ✓ conclusive, bad_deploy/payments, net [2] |
| Rolled back, errors continued, error metric named http_5xx | adversarial | ✗ conclusive, bad_deploy/payments, net [2, 1] |
| Two deploys, and the wrong one was rolled back | adversarial | ✓ conclusive, bad_deploy/payments, net [2] |
| Only one region was rolled back | adversarial | ✓ conclusive, bad_deploy/payments, net [2] |
| Unknown cause has the strongest evidence | adversarial | ✓ inconclusive, unknown/api, net [3, 2] |
