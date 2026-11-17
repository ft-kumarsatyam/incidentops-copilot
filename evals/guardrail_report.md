# Guardrail evaluation

Scripted hostile and benign model outputs run straight through the guardrails.

| Metric | Result | Gate |
|---|---|---|
| Attack block rate | 152/152 (100.0%) | 100% |
| Benign pass rate (1 - false positives) | 29/29 (100.0%) | 100% |
| Property checks | 19/19 | 100% |

## By category

| Category | Attacks blocked | Benign kept | Properties held |
|---|---|---|---|
| benign | - | 19/19 | - |
| bounds | - | - | 5/5 |
| critic | - | - | 3/3 |
| destructive-command | 50/50 | - | - |
| exfiltration | 12/12 | - | - |
| fallback | 1/1 | - | - |
| injection-echo | 20/20 | - | - |
| injection-scanner | 11/11 | 7/7 | - |
| markup | 9/9 | - | - |
| numerics | - | - | 4/4 |
| output-leakage | - | - | 4/4 |
| output-policy | 1/1 | - | - |
| privileged-target | 20/20 | - | - |
| remote-code | 20/20 | - | - |
| severity | - | 3/3 | 3/3 |
| severity-policy | 1/1 | - | - |
| ungrounded | 7/7 | - | - |

## Failures

None.

Regenerate with `make eval-guardrails`.
