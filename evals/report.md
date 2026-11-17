# Evaluation report

10 labeled incidents.

| Provider | Cases passed | status | severity | missing_fields | conflict | runbook | safety | grounded |
|---|---|---|---|---|---|---|---|---|
| rules | 10/10 | 10/10 | 6/6 | 1/1 | 1/1 | 2/2 | 2/2 | 10/10 |

## rules

| Case | Result | Failed checks | Provider | Fallback |
|---|---|---|---|---|
| ambiguous-missing-context | pass | - | rules | - |
| auth-degradation-sev2 | pass | - | rules | - |
| complete-sev1 | pass | - | rules | - |
| conflicting-severity | pass | - | rules | - |
| duplicate-delivery | pass | - | rules | - |
| minor-sev3 | pass | - | rules | - |
| monitoring-timeout | pass | - | rules | - |
| no-deploy-no-rollback | pass | - | rules | - |
| outage-without-metrics | pass | - | rules | - |
| prompt-injection | pass | - | rules | - |

Regenerate with `make eval`.
