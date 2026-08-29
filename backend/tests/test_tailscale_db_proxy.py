import socket
import threading
from pathlib import Path

import pytest

import tailscale_db_proxy as proxy


def test_config_defaults_and_overrides():
    defaults = proxy.ProxyConfig.from_env({})
    assert defaults.listen_host == "127.0.0.1"
    assert defaults.listen_port == 13306
    assert defaults.target_host == "100.74.151.0"
    assert defaults.target_port == 3306
    assert defaults.socks_host == "127.0.0.1"
    assert defaults.socks_port == 1055

    configured = proxy.ProxyConfig.from_env(
        {
            "TAILSCALE_DB_PROXY_PORT": "23306",
            "TAILSCALE_DB_HOST": "mysql.internal.example",
            "TAILSCALE_DB_PORT": "3307",
            "TAILSCALE_SOCKS_HOST": "localhost",
            "TAILSCALE_SOCKS_PORT": "2055",
            "TAILSCALE_CONNECT_TIMEOUT": "3.5",
        }
    )
    assert configured.listen_port == 23306
    assert configured.target_host == "mysql.internal.example"
    assert configured.target_port == 3307
    assert configured.socks_host == "localhost"
    assert configured.socks_port == 2055
    assert configured.connect_timeout == 3.5


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TAILSCALE_DB_PROXY_PORT", "not-a-port"),
        ("TAILSCALE_DB_PORT", "0"),
        ("TAILSCALE_SOCKS_PORT", "65536"),
    ],
)
def test_config_rejects_invalid_ports(name, value):
    with pytest.raises(ValueError):
        proxy.ProxyConfig.from_env({name: value})


def test_config_rejects_empty_target_host():
    with pytest.raises(ValueError, match="TAILSCALE_DB_HOST"):
        proxy.ProxyConfig.from_env({"TAILSCALE_DB_HOST": "  "})


def test_create_upstream_uses_tailscale_socks(monkeypatch):
    config = proxy.ProxyConfig.from_env({})
    calls = []

    class FakeSocket:
        def settimeout(self, value):
            calls.append(("settimeout", value))

    def fake_create_connection(destination, **kwargs):
        calls.append((destination, kwargs))
        return FakeSocket()

    monkeypatch.setattr(proxy.socks, "create_connection", fake_create_connection)
    upstream = proxy.create_upstream(config)

    assert isinstance(upstream, FakeSocket)
    destination, kwargs = calls[0]
    assert destination == ("100.74.151.0", 3306)
    assert kwargs["proxy_type"] == proxy.socks.SOCKS5
    assert kwargs["proxy_addr"] == "127.0.0.1"
    assert kwargs["proxy_port"] == 1055
    assert calls[1] == ("settimeout", None)


def test_check_upstream_closes_connection(monkeypatch):
    events = []

    class FakeSocket:
        def __enter__(self):
            events.append("enter")
            return self

        def __exit__(self, *_args):
            events.append("exit")

    monkeypatch.setattr(proxy, "create_upstream", lambda _config: FakeSocket())
    proxy.check_upstream(proxy.ProxyConfig.from_env({}))
    assert events == ["enter", "exit"]


def test_relay_forwards_both_directions():
    client, proxy_client = socket.socketpair()
    proxy_upstream, upstream = socket.socketpair()
    relay_thread = threading.Thread(
        target=proxy.relay,
        args=(proxy_client, proxy_upstream),
        daemon=True,
    )
    relay_thread.start()

    try:
        client.sendall(b"mysql request")
        assert upstream.recv(1024) == b"mysql request"

        upstream.sendall(b"mysql response")
        assert client.recv(1024) == b"mysql response"

        client.shutdown(socket.SHUT_WR)
        relay_thread.join(timeout=1)
        assert not relay_thread.is_alive()
    finally:
        client.close()
        proxy_client.close()
        proxy_upstream.close()
        upstream.close()


def test_threading_server_proxies_connection(monkeypatch):
    config = proxy.ProxyConfig.from_env({"TAILSCALE_DB_PROXY_PORT": "1"})
    config = proxy.ProxyConfig(
        **{**config.__dict__, "listen_port": 0}
    )
    proxy_upstream, upstream = socket.socketpair()
    monkeypatch.setattr(proxy, "create_upstream", lambda _config: proxy_upstream)

    server = proxy.ThreadingProxyServer(config)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        with socket.create_connection(server.server_address, timeout=1) as client:
            client.sendall(b"hello")
            assert upstream.recv(1024) == b"hello"
            upstream.sendall(b"world")
            assert client.recv(1024) == b"world"
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=1)
        upstream.close()


def test_serve_sets_and_removes_readiness_file(monkeypatch, tmp_path):
    events = []
    ready_file = tmp_path / "proxy.ready"

    class FakeServer:
        def __init__(self, config):
            events.append(("init", config))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            events.append("exit")

        def serve_forever(self):
            assert ready_file.exists()
            events.append("serve")

    monkeypatch.setattr(proxy, "ThreadingProxyServer", FakeServer)
    config = proxy.ProxyConfig.from_env({})
    proxy.serve(config, Path(ready_file))

    assert events == [("init", config), "serve", "exit"]
    assert not ready_file.exists()


def test_main_check_returns_failure_without_starting_server(monkeypatch):
    monkeypatch.setattr(
        proxy,
        "check_upstream",
        lambda _config: (_ for _ in ()).throw(OSError("denied")),
    )
    monkeypatch.setattr(
        proxy,
        "serve",
        lambda *_args: pytest.fail("server must not start in check mode"),
    )
    assert proxy.main(["--check"]) == 1


def test_main_check_returns_success(monkeypatch):
    checked = []
    monkeypatch.setattr(proxy, "check_upstream", lambda config: checked.append(config))
    assert proxy.main(["--check"]) == 0
    assert checked[0].target_host == "100.74.151.0"
