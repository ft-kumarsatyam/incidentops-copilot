from incidentops.rag import expand_runbook_query


def test_semantic_purchase_query_expands_checkout_terms() -> None:
    expanded = expand_runbook_query(
        "users cannot complete purchases",
        service="checkout-api",
    )
    assert "checkout" in expanded.lower()
    assert "payment" in expanded.lower()


def test_login_oauth_expands_auth_terms() -> None:
    expanded = expand_runbook_query("login broken OAuth", service="auth-api")
    assert "authentication" in expanded.lower() or "sign" in expanded.lower()
