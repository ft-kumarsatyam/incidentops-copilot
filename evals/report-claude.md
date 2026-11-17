# Core incident evaluation (rules vs Claude)

Ten original labeled incidents (`evals/dataset/*.json`, excluding guardrails).

Regenerate with `make eval-claude` (`--suite core --provider both`). When
`ANTHROPIC_API_KEY` is set, this file includes a Claude row; otherwise it records
the rules baseline only.

# Evaluation report

10 labeled incidents.

| Provider | Cases passed | status | severity | missing_fields | conflict | runbook | safety | grounded | bounded | no_leak | flagged | calibration | actions |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| rules | 10/10 | 10/10 | 6/6 | 1/1 | 1/1 | 2/2 | 2/2 | 10/10 | 10/10 | n/a | n/a | n/a | n/a |

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
