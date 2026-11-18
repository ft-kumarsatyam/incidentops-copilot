"""The API refuses to start in unsafe configurations."""

import pytest
from fastapi.testclient import TestClient

from incidentops.api.app import create_app
from incidentops.core.config import Settings


def test_production_needs_a_shared_key_or_oidc(settings: Settings) -> None:
    with pytest.raises(RuntimeError, match="INCIDENTOPS_API_KEY or OIDC_ISSUER"):
        create_app(settings.model_copy(update={"incidentops_env": "prod"}))


def test_oidc_without_an_audience_is_refused(settings: Settings) -> None:
    config = settings.model_copy(
        update={"incidentops_env": "prod", "oidc_issuer": "https://idp.example.com"}
    )
    with pytest.raises(RuntimeError, match="OIDC_AUDIENCE"):
        create_app(config)


def test_production_with_placeholder_secrets_is_refused(settings: Settings) -> None:
    config = settings.model_copy(
        update={"incidentops_env": "prod", "incidentops_api_key": "local-development-key"}
    )
    with pytest.raises(RuntimeError, match="INCIDENTOPS_API_KEY"):
        create_app(config)


def test_oidc_alone_is_enough_in_production(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from incidentops.security.oidc import OidcVerifier

    monkeypatch.setattr(
        "incidentops.api.app.OidcVerifier", lambda s: OidcVerifier(s, key_resolver=lambda kid: None)
    )
    config = settings.model_copy(
        update={
            "incidentops_env": "prod",
            "oidc_issuer": "https://idp.example.com",
            "oidc_audience": "incidentops",
        }
    )
    with TestClient(create_app(config)) as client:  # starts, and shuts down cleanly
        assert client.get("/health").status_code == 200
