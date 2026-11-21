"""Outbound address checks for the action webhook."""

import socket
from typing import Any

import pytest

from incidentops.core.ssrf import blocked_reason


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "https://169.254.1.1/x",
        "http://[fe80::1]/x",
        "http://[::ffff:169.254.169.254]/x",  # the metadata address in IPv4-mapped form
        "http://0.0.0.0/x",
        "http://224.0.0.1/x",
    ],
)
def test_dangerous_addresses_are_always_refused(url: str) -> None:
    assert blocked_reason(url) is not None
    assert blocked_reason(url, block_private=True) is not None


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.5/x",
        "http://192.168.1.1/x",
        "http://172.16.0.9/x",
        "http://127.0.0.1/x",
        "http://[::1]/x",
        "http://[fd00::1]/x",
    ],
)
def test_private_and_loopback_are_refused_only_when_asked(url: str) -> None:
    assert blocked_reason(url) is None
    assert "private" in (blocked_reason(url, block_private=True) or "")


def test_public_addresses_pass() -> None:
    assert blocked_reason("https://93.184.216.34/hook", block_private=True) is None


@pytest.mark.parametrize(
    "url", ["ftp://example.com/x", "file:///etc/passwd", "gopher://x/", "http:///x"]
)
def test_only_http_and_https_with_a_host(url: str) -> None:
    assert blocked_reason(url) is not None


def test_a_name_that_resolves_to_the_metadata_address_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(host: str, *args: Any, **kwargs: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)

    assert "resolves to" in (blocked_reason("https://hooks.example.com/x") or "")


def test_one_bad_answer_among_several_is_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(host: str, *args: Any, **kwargs: Any) -> list[Any]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake)

    assert blocked_reason("https://rebind.example.com/x") is not None


def test_an_unresolvable_name_is_left_to_fail_on_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(host: str, *args: Any, **kwargs: Any) -> list[Any]:
        raise socket.gaierror("nope")

    monkeypatch.setattr(socket, "getaddrinfo", fake)

    assert blocked_reason("https://nowhere.invalid/x") is None
