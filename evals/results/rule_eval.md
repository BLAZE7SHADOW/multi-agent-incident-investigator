# Evidence rule evaluation

Scores the evidence rules alone on scenarios from `data/rule_eval.yaml`, with no model calls. Labels are what a perfect Verifier would give, so this shows what the rules decide, not what the full system would do with a real model.

| Metric | Before | After |
|---|---|---|
| Scenarios correct | 46% | 92% |
| Precision | 56% | 91% |
| Recall | 50% | 100% |
| Wrong conclusions | 4 | 1 |
| True positives (right cause concluded) | 5 | 10 |
| False positives (wrong conclusion) | 4 | 1 |
| False negatives (right cause missed) | 5 | 0 |
| True negatives (rightly inconclusive) | 1 | 2 |

Precision = TP / (TP + FP). Recall = TP / (TP + FN). A wrong conclusion counts as both a false positive and a false negative, because it also misses the right cause.

## Scenarios

| Scenario | Origin | Before | After |
|---|---|---|---|
| INC-11, unevidenced config rival ties the real cause | reconstructed from multi-agent run 1 | ✗ inconclusive, resource_exhaustion/inventory, net [2, 2] | ✓ conclusive, resource_exhaustion/inventory, net [2, 0] |
| INC-11, "unknown" rival ties the real cause | reconstructed from multi-agent run 2 | ✗ inconclusive, resource_exhaustion/inventory, net [2, 2] | ✓ conclusive, resource_exhaustion/inventory, net [2, 2] |
| INC-09, unevidenced config rival with 6 rows ties the real cause | reconstructed from multi-agent run 0 | ✗ inconclusive, resource_exhaustion/auth, net [6, 6] | ✓ conclusive, resource_exhaustion/auth, net [6, 0] |
| INC-12, rolled-back deploy beats the real cause by 1 | reconstructed from multi-agent run 0 | ✗ conclusive, bad_deploy/payments, net [4, 3] | ✓ conclusive, resource_exhaustion/postgres, net [3, -1] |
| INC-12, rolled-back deploy beats the real cause by 1, smaller | reconstructed from multi-agent run 1 | ✗ conclusive, bad_deploy/payments, net [3, 2] | ✓ conclusive, resource_exhaustion/postgres, net [2, -1] |
| INC-01 control, real deploy cause must still conclude | control | ✓ conclusive, bad_deploy/payments, net [3, 0] | ✓ conclusive, bad_deploy/payments, net [3, 0] |
| INC-02 control, real config cause must still conclude | control | ✓ conclusive, config_change/inventory, net [3, 0] | ✓ conclusive, config_change/inventory, net [3, 0] |
| INC-05 control, regional config cause must still conclude | control | ✓ conclusive, config_change/gateway, net [3, 0] | ✓ conclusive, config_change/gateway, net [3, 0] |
| INC-03 control, harmless deploy must not win | control | ✓ conclusive, resource_exhaustion/redis, net [3, 1] | ✓ conclusive, resource_exhaustion/redis, net [3, 1] |
| INC-12 control, a rollback that fixed errors must not block a real deploy cause | control, with error_rate changed to recover after the rollback | ✓ conclusive, bad_deploy/payments, net [3, 1] | ✓ conclusive, bad_deploy/payments, net [3, 1] |
| INC-08 control, deploy blamed with no deployment data must not conclude | control | ✗ conclusive, bad_deploy/payments, net [2] | ✓ inconclusive, bad_deploy/payments, net [0] |
| INC-10 known gap, a 3-day-old deploy is blamed for a brief blip (known gap) | known gap; the rules do not check how long before the incident a change happened | ✗ conclusive, bad_deploy/checkout, net [2] | ✗ conclusive, bad_deploy/checkout, net [2] |
| INC-10 control, the critic's "insufficient" must hold | control | ✓ inconclusive, bad_deploy/checkout, net [2] | ✓ inconclusive, bad_deploy/checkout, net [2] |
