# Runbook retrieval evaluation

11 labeled queries against the repo runbooks.

| Mode | Mean recall@k | Mean MRR | Top-1 rate |
|---|---:|---:|---:|
| fts | 81.82% | 0.818 | 81.82% |
| rewrite | 100.00% | 1.000 | 100.00% |
| hybrid | 100.00% | 1.000 | 100.00% |

## fts

| Case | recall@k | MRR | top-1 | first hit |
|---|---:|---:|---|---|
| auth-feature-flag | 100% | 1.00 | yes | auth-api.md |
| auth-identity-vocabulary | 100% | 1.00 | yes | auth-api.md |
| auth-idp-status | 100% | 1.00 | yes | auth-api.md |
| auth-semantic-login | 0% | 0.00 | no | - |
| auth-service-prefer | 100% | 1.00 | yes | auth-api.md |
| auth-token-refresh | 100% | 1.00 | yes | auth-api.md |
| checkout-deploy-symptom | 100% | 1.00 | yes | checkout-api.md |
| checkout-payment-vocabulary | 100% | 1.00 | yes | checkout-api.md |
| checkout-rollback-deploy | 100% | 1.00 | yes | checkout-api.md |
| checkout-section-mitigation | 100% | 1.00 | yes | checkout-api.md |
| checkout-semantic-purchase | 0% | 0.00 | no | - |

## rewrite

| Case | recall@k | MRR | top-1 | first hit |
|---|---:|---:|---|---|
| auth-feature-flag | 100% | 1.00 | yes | auth-api.md |
| auth-identity-vocabulary | 100% | 1.00 | yes | auth-api.md |
| auth-idp-status | 100% | 1.00 | yes | auth-api.md |
| auth-semantic-login | 100% | 1.00 | yes | auth-api.md |
| auth-service-prefer | 100% | 1.00 | yes | auth-api.md |
| auth-token-refresh | 100% | 1.00 | yes | auth-api.md |
| checkout-deploy-symptom | 100% | 1.00 | yes | checkout-api.md |
| checkout-payment-vocabulary | 100% | 1.00 | yes | checkout-api.md |
| checkout-rollback-deploy | 100% | 1.00 | yes | checkout-api.md |
| checkout-section-mitigation | 100% | 1.00 | yes | checkout-api.md |
| checkout-semantic-purchase | 100% | 1.00 | yes | checkout-api.md |

## hybrid

| Case | recall@k | MRR | top-1 | first hit |
|---|---:|---:|---|---|
| auth-feature-flag | 100% | 1.00 | yes | auth-api.md |
| auth-identity-vocabulary | 100% | 1.00 | yes | auth-api.md |
| auth-idp-status | 100% | 1.00 | yes | auth-api.md |
| auth-semantic-login | 100% | 1.00 | yes | auth-api.md |
| auth-service-prefer | 100% | 1.00 | yes | auth-api.md |
| auth-token-refresh | 100% | 1.00 | yes | auth-api.md |
| checkout-deploy-symptom | 100% | 1.00 | yes | checkout-api.md |
| checkout-payment-vocabulary | 100% | 1.00 | yes | checkout-api.md |
| checkout-rollback-deploy | 100% | 1.00 | yes | checkout-api.md |
| checkout-section-mitigation | 100% | 1.00 | yes | checkout-api.md |
| checkout-semantic-purchase | 100% | 1.00 | yes | checkout-api.md |

## Adoption gate

Hybrid recall@k minus raw FTS: **+18.18%** (hybrid default gate: ≥10%).
Query rewrite mean recall@k: **100.00%** — production uses FTS + rewrite; hybrid optional ([ADR 007](../docs/adr/007-hybrid-runbook-retrieval.md)).

Regenerate with `make eval-retrieval`.
