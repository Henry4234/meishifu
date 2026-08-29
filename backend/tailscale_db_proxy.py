"""將本機 MySQL TCP 連線透過 Tailscale userspace SOCKS5 送進 tailnet。"""

from __future__ import annotations

import argparse
import logging
import os
import select
import socket
import socketserver
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import socks


LOGGER = logging.getLogger("tailscale-db-proxy")
BUFFER_SIZE = 64 * 1024


def _read_port(env: Mapping[str, str], name: str, default: int) -> int:
    raw_value = env.get(name, str(default))
    try:
        port = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{name} must be between 1 and 65535")
    return port


@dataclass(frozen=True)
class ProxyConfig:
    """Runtime configuration for the loopback-only TCP proxy."""

    listen_host: str
    listen_port: int
    target_host: str
    target_port: int
    socks_host: str
    socks_port: int
    connect_timeout: float

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ProxyConfig":
        values = os.environ if env is None else env
        target_host = values.get("TAILSCALE_DB_HOST", "100.74.151.0").strip()
        if not target_host:
            raise ValueError("TAILSCALE_DB_HOST must not be empty")

        return cls(
            # 固定只監聽 loopback，避免 Cloud Run container port 意外暴露 proxy。
            listen_host="127.0.0.1",
            listen_port=_read_port(values, "TAILSCALE_DB_PROXY_PORT", 13306),
            target_host=target_host,
            target_port=_read_port(values, "TAILSCALE_DB_PORT", 3306),
            socks_host=values.get("TAILSCALE_SOCKS_HOST", "127.0.0.1").strip()
            or "127.0.0.1",
            socks_port=_read_port(values, "TAILSCALE_SOCKS_PORT", 1055),
            connect_timeout=float(values.get("TAILSCALE_CONNECT_TIMEOUT", "10")),
        )


def create_upstream(config: ProxyConfig) -> socket.socket:
    """Open the DB connection through tailscaled's local SOCKS5 endpoint."""
    upstream = socks.create_connection(
        (config.target_host, config.target_port),
        proxy_type=socks.SOCKS5,
        proxy_addr=config.socks_host,
        proxy_port=config.socks_port,
        proxy_rdns=True,
        timeout=config.connect_timeout,
    )
    # The timeout protects connection establishment only. PyMySQL owns query timeouts.
    upstream.settimeout(None)
    return upstream


def check_upstream(config: ProxyConfig) -> None:
    """Verify that the tailnet route, access rule, and MySQL TCP port are reachable."""
    with create_upstream(config):
        LOGGER.info(
            "Successfully reached MySQL at %s:%s through Tailscale",
            config.target_host,
            config.target_port,
        )


def relay(client: socket.socket, upstream: socket.socket) -> None:
    """Relay bytes in both directions until either side closes the connection."""
    peers = {client: upstream, upstream: client}
    sockets = tuple(peers)

    while True:
        readable, _, exceptional = select.select(sockets, (), sockets)
        if exceptional:
            return
        for source in readable:
            try:
                payload = source.recv(BUFFER_SIZE)
                if not payload:
                    return
                peers[source].sendall(payload)
            except OSError:
                return


class SocksProxyHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        config = self.server.proxy_config  # type: ignore[attr-defined]
        try:
            with create_upstream(config) as upstream:
                relay(self.request, upstream)
        except OSError as exc:
            LOGGER.warning(
                "Unable to proxy MySQL connection to %s:%s: %s",
                config.target_host,
                config.target_port,
                exc,
            )


class ThreadingProxyServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False

    def __init__(self, config: ProxyConfig):
        self.proxy_config = config
        super().__init__((config.listen_host, config.listen_port), SocksProxyHandler)


def serve(config: ProxyConfig, ready_file: Path | None = None) -> None:
    with ThreadingProxyServer(config) as server:
        LOGGER.info(
            "Listening on %s:%s and forwarding to %s:%s via SOCKS5 %s:%s",
            config.listen_host,
            config.listen_port,
            config.target_host,
            config.target_port,
            config.socks_host,
            config.socks_port,
        )
        if ready_file is not None:
            ready_file.touch()
        try:
            server.serve_forever()
        finally:
            if ready_file is not None:
                ready_file.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the SOCKS5 path to MySQL and exit",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    ready_file_value = os.getenv(
        "TAILSCALE_DB_PROXY_READY_FILE", "/tmp/tailscale-db-proxy.ready"
    )
    config = ProxyConfig.from_env()
    if args.check:
        try:
            check_upstream(config)
        except OSError as exc:
            LOGGER.error(
                "Cannot reach MySQL at %s:%s through Tailscale: %s",
                config.target_host,
                config.target_port,
                exc,
            )
            return 1
        return 0

    serve(config, Path(ready_file_value) if ready_file_value else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
