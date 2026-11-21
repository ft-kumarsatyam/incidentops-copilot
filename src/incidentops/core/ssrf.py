"""Refuse to call addresses an action webhook has no business reaching (SSRF defence).

The dispatcher posts incident data to a URL from configuration. If that setting were ever
pointed (or tricked) at the cloud metadata service or an internal admin port, the service would
leak credentials or hit internal systems on an attacker's behalf. This resolves the host and
refuses dangerous addresses *in addition to* the OUTBOUND_ALLOWED_HOSTS list.

Always refused: link-local (169.254.0.0/16 and fe80::/10, which holds the cloud metadata
endpoints), unspecified (0.0.0.0, ::) and multicast. With ``block_private`` also loopback and the
private ranges. Only http and https are allowed.

Limit: the check resolves the name, then httpx resolves it again to connect, so a DNS answer that
changes between the two is not caught. Pin the host with OUTBOUND_ALLOWED_HOSTS and use a network
egress policy for a hard guarantee.
"""

import ipaddress
import socket
from urllib.parse import urlsplit

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def _unwrap(address: IPAddress) -> IPAddress:
    """``::ffff:169.254.169.254`` is the metadata address in disguise."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _refusal(address: IPAddress, block_private: bool) -> str | None:
    address = _unwrap(address)
    if address.is_link_local:
        return "link-local address (cloud metadata range)"
    if address.is_unspecified:
        return "unspecified address"
    if address.is_multicast:
        return "multicast address"
    if block_private and (address.is_loopback or address.is_private):
        return "private or loopback address"
    return None


def _addresses(host: str) -> list[IPAddress]:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []  # unresolvable: the request itself will fail, there is nothing to refuse
    found: list[IPAddress] = []
    for info in infos:
        try:
            found.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue
    return found


def blocked_reason(url: str, *, block_private: bool = False) -> str | None:
    """Why this URL must not be called, or ``None`` if it may be."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return f"scheme {parts.scheme!r} is not http or https"
    host = parts.hostname
    if not host:
        return "URL has no host"
    for address in _addresses(host):
        reason = _refusal(address, block_private)
        if reason:
            return f"{host} resolves to a {reason}"
    return None
