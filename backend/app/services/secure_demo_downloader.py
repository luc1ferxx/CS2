from __future__ import annotations

import hashlib
import http.client
import ipaddress
import math
import os
import queue
import socket
import ssl
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import (
    BinaryIO,
    Callable,
    ContextManager,
    Iterable,
    Iterator,
    Mapping,
    Protocol,
)
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

from app.services.demo_source_provider import DemoSource


DEFAULT_DEMO_CONTENT_TYPES = frozenset(
    {
        "application/octet-stream",
        "application/vnd.valve.source2.demo",
    }
)


class DemoDownloadError(RuntimeError):
    def __init__(self, code: str, safe_message: str):
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)


class DemoDownloadConfigurationError(RuntimeError):
    pass


class _DeadlineBudget:
    def __init__(
        self,
        *,
        deadline_at: float,
        monotonic: Callable[[], float],
    ) -> None:
        self.deadline_at = float(deadline_at)
        self.monotonic = monotonic

    def remaining_seconds(self) -> float:
        remaining = self.deadline_at - float(self.monotonic())
        if not math.isfinite(remaining) or remaining <= 0:
            raise DemoDownloadError(
                "demo_download_timeout",
                "Demo download timed out.",
            )
        return remaining

    def bounded_timeout(self, configured_seconds: float) -> float:
        return min(float(configured_seconds), self.remaining_seconds())


@dataclass(frozen=True)
class DemoDownloadPolicy:
    exact_hosts: frozenset[str]
    max_bytes: int
    allowed_content_types: frozenset[str] = DEFAULT_DEMO_CONTENT_TYPES
    max_redirects: int = 3
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 10.0
    total_timeout_seconds: float = 30.0
    chunk_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        normalized_hosts = frozenset(_normalize_hostname(host) for host in self.exact_hosts)
        if not normalized_hosts or normalized_hosts != self.exact_hosts:
            raise ValueError("Demo download allowlist must contain normalized exact hosts")
        if not 16 <= self.max_bytes <= 1024 * 1024 * 1024:
            raise ValueError("Demo download size limit is invalid")
        if not 0 <= self.max_redirects <= 3:
            raise ValueError("Demo download redirect limit is invalid")
        if not 0.1 <= self.connect_timeout_seconds <= 30:
            raise ValueError("Demo download connect timeout is invalid")
        if not 0.1 <= self.read_timeout_seconds <= 30:
            raise ValueError("Demo download read timeout is invalid")
        if not (
            max(self.connect_timeout_seconds, self.read_timeout_seconds)
            <= self.total_timeout_seconds
            <= 120
        ):
            raise ValueError("Demo download total timeout is invalid")
        if not 4096 <= self.chunk_bytes <= 1024 * 1024:
            raise ValueError("Demo download chunk size is invalid")


class AddressResolver(Protocol):
    def resolve(
        self,
        hostname: str,
        port: int,
        *,
        timeout_seconds: float,
    ) -> tuple[str, ...]: ...


class StreamingResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def iter_raw(self, chunk_size: int) -> Iterable[bytes]: ...

    def __enter__(self) -> "StreamingResponse": ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> object: ...


class PinnedHttpsTransport(Protocol):
    def open(self, **kwargs: object) -> ContextManager[StreamingResponse]: ...


@dataclass(frozen=True)
class DownloadedDemo:
    path: Path
    stream: BinaryIO
    filename: str
    content_type: str
    size_bytes: int
    sha256: str


class SystemAddressResolver:
    _lookup_slots = threading.BoundedSemaphore(8)

    def resolve(
        self,
        hostname: str,
        port: int,
        *,
        timeout_seconds: float,
    ) -> tuple[str, ...]:
        lookup_timeout = float(timeout_seconds)
        if not math.isfinite(lookup_timeout) or lookup_timeout <= 0:
            raise DemoDownloadError(
                "demo_download_timeout",
                "Demo download timed out.",
            )
        if not self._lookup_slots.acquire(blocking=False):
            raise DemoDownloadError(
                "demo_source_unreachable",
                "Demo source could not be resolved safely.",
            )
        results: queue.Queue[object] = queue.Queue(maxsize=1)

        def resolve_in_background() -> None:
            try:
                results.put(
                    socket.getaddrinfo(
                        hostname,
                        port,
                        type=socket.SOCK_STREAM,
                        proto=socket.IPPROTO_TCP,
                    )
                )
            except BaseException as exc:
                results.put(exc)
            finally:
                self._lookup_slots.release()

        thread = threading.Thread(
            target=resolve_in_background,
            name="steam-demo-dns",
            daemon=True,
        )
        try:
            thread.start()
        except Exception:
            self._lookup_slots.release()
            raise DemoDownloadError(
                "demo_source_unreachable",
                "Demo source could not be resolved safely.",
            ) from None
        try:
            resolved = results.get(timeout=lookup_timeout)
        except queue.Empty:
            raise DemoDownloadError(
                "demo_download_timeout",
                "Demo download timed out.",
            ) from None
        if isinstance(resolved, BaseException):
            raise DemoDownloadError(
                "demo_source_unreachable",
                "Demo source could not be resolved safely.",
            ) from None
        records = resolved
        addresses: list[str] = []
        for _family, _socktype, _protocol, _canonical_name, socket_address in records:
            if not socket_address:
                continue
            value = str(socket_address[0])
            if value not in addresses:
                addresses.append(value)
        return tuple(addresses)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        *,
        connect_ip: str,
        tls_server_name: str,
        port: int = 443,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        deadline_at: float,
        monotonic: Callable[[], float],
        ssl_context: ssl.SSLContext | None = None,
    ):
        deadline = _DeadlineBudget(
            deadline_at=deadline_at,
            monotonic=monotonic,
        )
        context = ssl_context or ssl.create_default_context()
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        super().__init__(
            tls_server_name,
            port=port,
            timeout=deadline.bounded_timeout(connect_timeout_seconds),
            context=context,
        )
        self._connect_ip = str(ipaddress.ip_address(connect_ip))
        self._tls_server_name = tls_server_name
        self._connect_timeout_seconds = connect_timeout_seconds
        self._read_timeout_seconds = read_timeout_seconds
        self._deadline = deadline

    def connect(self) -> None:
        raw_socket: socket.socket | None = None
        tls_socket: ssl.SSLSocket | None = None
        try:
            raw_socket = socket.create_connection(
                (self._connect_ip, self.port),
                timeout=self._deadline.bounded_timeout(
                    self._connect_timeout_seconds
                ),
            )
            self._deadline.remaining_seconds()
            _require_expected_peer(raw_socket, self._connect_ip)
            raw_socket.settimeout(
                self._deadline.bounded_timeout(self._read_timeout_seconds)
            )
            tls_socket = self._context.wrap_socket(
                raw_socket,
                server_hostname=self._tls_server_name,
            )
            raw_socket = None
            self._deadline.remaining_seconds()
            _require_expected_peer(tls_socket, self._connect_ip)
            self.sock = tls_socket
            tls_socket = None
            self.set_read_deadline_timeout()
        except Exception:
            if tls_socket is not None:
                tls_socket.close()
            if raw_socket is not None:
                raw_socket.close()
            raise

    def set_read_deadline_timeout(self) -> float:
        timeout_seconds = self._deadline.bounded_timeout(
            self._read_timeout_seconds
        )
        if self.sock is not None:
            self.sock.settimeout(timeout_seconds)
        return timeout_seconds


class _StdlibStreamingResponse:
    def __init__(
        self,
        response: object,
        *,
        deadline: _DeadlineBudget,
        read_timeout_seconds: float,
        timeout_setter: Callable[[], object] | None,
    ):
        self._response = response
        self._deadline = deadline
        self._read_timeout_seconds = read_timeout_seconds
        self._timeout_setter = timeout_setter
        self.status_code = int(getattr(response, "status"))
        self.headers = _strict_response_headers(getattr(response, "headers"))

    def iter_raw(self, chunk_size: int) -> Iterable[bytes]:
        read1 = getattr(self._response, "read1", None)
        reader = read1 if callable(read1) else getattr(self._response, "read")
        while True:
            if self._timeout_setter is not None:
                self._timeout_setter()
            else:
                self._deadline.bounded_timeout(self._read_timeout_seconds)
            try:
                chunk = reader(chunk_size)
            except (TimeoutError, socket.timeout):
                raise DemoDownloadError(
                    "demo_download_timeout",
                    "Demo download timed out.",
                ) from None
            self._deadline.remaining_seconds()
            if not chunk:
                return
            yield chunk


class StdlibPinnedHttpsTransport:
    def __init__(
        self,
        *,
        connection_factory: object = _PinnedHTTPSConnection,
    ):
        self.connection_factory = connection_factory

    @contextmanager
    def open(
        self,
        *,
        logical_url: str,
        connect_ip: str,
        tls_server_name: str,
        host_header: str,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        deadline_at: float,
        monotonic: Callable[[], float],
    ) -> Iterator[_StdlibStreamingResponse]:
        deadline = _DeadlineBudget(
            deadline_at=deadline_at,
            monotonic=monotonic,
        )
        try:
            parsed = urlsplit(logical_url)
            hostname = _normalize_hostname(parsed.hostname or "")
            normalized_tls_name = _normalize_hostname(tls_server_name)
            normalized_host_header = _normalize_hostname(host_header)
            address = ipaddress.ip_address(connect_ip)
        except (TypeError, UnicodeError, ValueError):
            raise DemoDownloadError(
                "demo_source_rejected",
                "Demo source URL was rejected.",
            ) from None
        if (
            parsed.scheme != "https"
            or hostname != normalized_tls_name
            or hostname != normalized_host_header
            or not _is_safe_global_address(address)
        ):
            raise DemoDownloadError(
                "demo_source_rejected",
                "Demo source URL was rejected.",
            )
        target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        connection = None
        response = None
        try:
            connection = self.connection_factory(
                connect_ip=str(address),
                tls_server_name=hostname,
                port=443,
                connect_timeout_seconds=connect_timeout_seconds,
                read_timeout_seconds=read_timeout_seconds,
                deadline_at=deadline_at,
                monotonic=monotonic,
            )
            connection.request(
                "GET",
                target,
                headers={
                    "Accept": "application/octet-stream",
                    "Accept-Encoding": "identity",
                    "Host": hostname,
                    "User-Agent": "CS2DemoCoach/1.0",
                },
            )
            deadline.remaining_seconds()
            set_read_timeout = getattr(
                connection,
                "set_read_deadline_timeout",
                None,
            )
            if callable(set_read_timeout):
                set_read_timeout()
            else:
                deadline.bounded_timeout(read_timeout_seconds)
            response = connection.getresponse()
            deadline.remaining_seconds()
            yield _StdlibStreamingResponse(
                response,
                deadline=deadline,
                read_timeout_seconds=read_timeout_seconds,
                timeout_setter=(
                    set_read_timeout if callable(set_read_timeout) else None
                ),
            )
        except DemoDownloadError:
            raise
        except (TimeoutError, socket.timeout):
            raise DemoDownloadError(
                "demo_download_timeout",
                "Demo download timed out.",
            ) from None
        except Exception:
            raise DemoDownloadError(
                "demo_source_unreachable",
                "Demo source could not be reached safely.",
            ) from None
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


class SecureDemoDownloader:
    def __init__(
        self,
        *,
        resolver: AddressResolver,
        transport: PinnedHttpsTransport,
        policy: DemoDownloadPolicy,
        scratch_root: Path | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.resolver = resolver
        self.transport = transport
        self.policy = policy
        self.scratch_root = scratch_root
        self.monotonic = monotonic

    @contextmanager
    def download(self, source: DemoSource) -> Iterator[DownloadedDemo]:
        started_at = float(self.monotonic())
        deadline_at = started_at + self.policy.total_timeout_seconds
        current_url = source.url
        redirect_count = 0
        while True:
            self._check_deadline(deadline_at)
            parsed = self._validate_url(current_url)
            resolver_timeout = min(
                self.policy.connect_timeout_seconds,
                self._remaining_seconds(deadline_at),
            )
            if resolver_timeout <= 0:
                raise DemoDownloadError(
                    "demo_download_timeout",
                    "Demo download timed out.",
                )
            addresses = self._resolve_public_addresses(
                parsed.hostname or "",
                443,
                timeout_seconds=resolver_timeout,
            )
            self._check_deadline(deadline_at)
            connect_ip = addresses[0]

            with self.transport.open(
                logical_url=current_url,
                connect_ip=connect_ip,
                tls_server_name=parsed.hostname,
                host_header=parsed.hostname,
                connect_timeout_seconds=self.policy.connect_timeout_seconds,
                read_timeout_seconds=self.policy.read_timeout_seconds,
                deadline_at=deadline_at,
                monotonic=self.monotonic,
            ) as response:
                status_code = int(response.status_code)
                if status_code in {301, 302, 303, 307, 308}:
                    if redirect_count >= self.policy.max_redirects:
                        raise DemoDownloadError(
                            "demo_redirect_rejected",
                            "Demo source redirected too many times.",
                        )
                    location = str(response.headers.get("Location", "")).strip()
                    if not location or len(location) > 4096:
                        raise DemoDownloadError(
                            "demo_redirect_rejected",
                            "Demo source redirect was rejected.",
                        )
                    current_url = urljoin(current_url, location)
                    redirect_count += 1
                    continue
                if status_code != 200:
                    raise DemoDownloadError(
                        "demo_download_failed",
                        "Demo source did not return a downloadable file.",
                    )
                content_type, content_length = self._validate_response_headers(
                    response.headers
                )

                scratch_parent = str(self.scratch_root) if self.scratch_root else None
                with tempfile.TemporaryDirectory(
                    prefix="cs2-demo-download-",
                    dir=scratch_parent,
                ) as directory:
                    os.chmod(directory, 0o700)
                    scratch_path = Path(directory) / "source.dem"
                    flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
                    flags |= getattr(os, "O_CLOEXEC", 0)
                    flags |= getattr(os, "O_NOFOLLOW", 0)
                    fd = os.open(scratch_path, flags, 0o600)
                    stream = os.fdopen(fd, "w+b")
                    digest = hashlib.sha256()
                    size_bytes = 0
                    try:
                        try:
                            for chunk in response.iter_raw(self.policy.chunk_bytes):
                                self._check_deadline(deadline_at)
                                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                                    raise DemoDownloadError(
                                        "demo_download_interrupted",
                                        "Demo download was interrupted.",
                                    )
                                normalized = bytes(chunk)
                                if not normalized:
                                    continue
                                size_bytes += len(normalized)
                                if size_bytes > self.policy.max_bytes:
                                    raise DemoDownloadError(
                                        "demo_download_too_large",
                                        "Demo download exceeds the configured size limit.",
                                    )
                                stream.write(normalized)
                                digest.update(normalized)
                        except DemoDownloadError:
                            raise
                        except Exception:
                            raise DemoDownloadError(
                                "demo_download_interrupted",
                                "Demo download was interrupted.",
                            ) from None
                        self._check_deadline(deadline_at)
                        if size_bytes < 16:
                            raise DemoDownloadError(
                                "demo_download_truncated",
                                "Demo download was empty or incomplete.",
                            )
                        if content_length is not None and content_length != size_bytes:
                            raise DemoDownloadError(
                                "demo_download_truncated",
                                "Demo download was empty or incomplete.",
                            )
                        stream.flush()
                        self._check_deadline(deadline_at)
                        os.fsync(stream.fileno())
                        self._check_deadline(deadline_at)
                        stream.seek(0)
                        yield DownloadedDemo(
                            path=scratch_path,
                            stream=stream,
                            filename=source.filename,
                            content_type=content_type,
                            size_bytes=size_bytes,
                            sha256=digest.hexdigest(),
                        )
                        return
                    finally:
                        stream.close()

    def _validate_url(self, value: str) -> SplitResult:
        if (
            len(value) > 4096
            or "\\" in value
            or any(character.isspace() or ord(character) < 32 for character in value)
        ):
            raise DemoDownloadError("demo_source_rejected", "Demo source URL was rejected.")
        try:
            parsed = urlsplit(value)
            hostname = _normalize_hostname(parsed.hostname or "")
            port = parsed.port
        except (UnicodeError, ValueError):
            raise DemoDownloadError("demo_source_rejected", "Demo source URL was rejected.") from None
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or port not in {None, 443}
            or hostname not in self.policy.exact_hosts
        ):
            raise DemoDownloadError("demo_source_rejected", "Demo source URL was rejected.")
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            raise DemoDownloadError("demo_source_rejected", "Demo source URL was rejected.")
        return parsed._replace(netloc=hostname)

    def _resolve_public_addresses(
        self,
        hostname: str,
        port: int,
        *,
        timeout_seconds: float,
    ) -> tuple[str, ...]:
        try:
            addresses = tuple(
                self.resolver.resolve(
                    hostname,
                    port,
                    timeout_seconds=timeout_seconds,
                )
            )
        except DemoDownloadError:
            raise
        except (TimeoutError, socket.timeout):
            raise DemoDownloadError(
                "demo_download_timeout",
                "Demo download timed out.",
            ) from None
        except Exception:
            raise DemoDownloadError(
                "demo_source_unreachable",
                "Demo source could not be resolved safely.",
            ) from None
        if not addresses or len(addresses) > 16:
            raise DemoDownloadError(
                "demo_source_unreachable",
                "Demo source could not be resolved safely.",
            )
        normalized: list[str] = []
        for value in addresses:
            try:
                address = ipaddress.ip_address(value)
            except ValueError:
                raise DemoDownloadError(
                    "demo_source_rejected",
                    "Demo source resolved to a prohibited address.",
                ) from None
            if not _is_safe_global_address(address):
                raise DemoDownloadError(
                    "demo_source_rejected",
                    "Demo source resolved to a prohibited address.",
                )
            canonical = str(address)
            if canonical not in normalized:
                normalized.append(canonical)
        return tuple(normalized)

    def _validate_response_headers(
        self,
        headers: Mapping[str, str],
    ) -> tuple[str, int | None]:
        transfer_encoding = str(headers.get("Transfer-Encoding", "")).strip().lower()
        if transfer_encoding not in {"", "chunked"}:
            raise DemoDownloadError(
                "demo_download_headers_rejected",
                "Demo download response headers were rejected.",
            )
        content_encoding = str(headers.get("Content-Encoding", "identity")).strip().lower()
        if content_encoding not in {"", "identity"}:
            raise DemoDownloadError(
                "demo_download_compression_rejected",
                "Compressed Demo downloads are not accepted.",
            )
        content_type = str(headers.get("Content-Type", "")).split(";", 1)[0].strip().lower()
        if content_type not in self.policy.allowed_content_types:
            raise DemoDownloadError(
                "demo_download_type_rejected",
                "Demo download response type was rejected.",
            )
        raw_length = headers.get("Content-Length")
        if transfer_encoding and raw_length is not None:
            raise DemoDownloadError(
                "demo_download_headers_rejected",
                "Demo download response headers were rejected.",
            )
        if raw_length is None:
            return content_type, None
        normalized_length = str(raw_length).strip()
        if not normalized_length.isdecimal() or len(normalized_length) > 20:
            raise DemoDownloadError(
                "demo_download_invalid_length",
                "Demo download response length was invalid.",
            )
        content_length = int(normalized_length)
        if content_length > self.policy.max_bytes:
            raise DemoDownloadError(
                "demo_download_too_large",
                "Demo download exceeds the configured size limit.",
            )
        return content_type, content_length

    def _check_deadline(self, deadline_at: float) -> None:
        if self._remaining_seconds(deadline_at) <= 0:
            raise DemoDownloadError(
                "demo_download_timeout",
                "Demo download timed out.",
            )

    def _remaining_seconds(self, deadline_at: float) -> float:
        return deadline_at - float(self.monotonic())


def _normalize_hostname(value: str) -> str:
    normalized = value.strip().rstrip(".").lower()
    if not normalized or len(normalized) > 253:
        raise ValueError("hostname is invalid")
    ascii_hostname = normalized.encode("idna").decode("ascii")
    labels = ascii_hostname.split(".")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        for label in labels
    ):
        raise ValueError("hostname is invalid")
    return ascii_hostname


def _is_safe_global_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if (
        not address.is_global
        or address.is_multicast
        or address.is_unspecified
        or address.is_loopback
        or address.is_link_local
        or address.is_private
        or address.is_reserved
    ):
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.is_site_local:
            return False
        if address.ipv4_mapped is not None or address.scope_id is not None:
            return False
        if address.sixtofour is not None or address.teredo is not None:
            return False
        if address in ipaddress.ip_network("64:ff9b::/96"):
            return False
        if address in ipaddress.ip_network("64:ff9b:1::/48"):
            return False
    return True


def _require_expected_peer(sock: object, expected_ip: str) -> None:
    try:
        peer = sock.getpeername()
        actual_ip = str(ipaddress.ip_address(peer[0]))
    except Exception:
        raise OSError("Pinned Demo connection peer could not be verified") from None
    if actual_ip != str(ipaddress.ip_address(expected_ip)):
        raise OSError("Pinned Demo connection peer did not match")


def _strict_response_headers(headers: object) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in (
        "Content-Type",
        "Content-Length",
        "Content-Encoding",
        "Transfer-Encoding",
        "Location",
    ):
        values: list[str]
        get_all = getattr(headers, "get_all", None)
        if callable(get_all):
            values = [str(value) for value in (get_all(name) or [])]
        else:
            get = getattr(headers, "get", None)
            value = get(name) if callable(get) else None
            values = [] if value is None else [str(value)]
        if len(values) > 1:
            raise DemoDownloadError(
                "demo_download_headers_rejected",
                "Demo download response headers were rejected.",
            )
        if values:
            result[name] = values[0]
    return result


def secure_demo_downloader_from_settings(
    runtime_settings: object,
    *,
    scratch_root: Path | None = None,
) -> SecureDemoDownloader:
    exact_hosts = frozenset(
        getattr(runtime_settings, "steam_demo_download_allowed_hosts", frozenset())
    )
    if not exact_hosts:
        raise DemoDownloadConfigurationError(
            "A licensed Demo provider exact-host allowlist is required"
        )
    policy = DemoDownloadPolicy(
        exact_hosts=exact_hosts,
        max_bytes=int(
            getattr(runtime_settings, "steam_demo_download_max_bytes")
        ),
        max_redirects=int(
            getattr(runtime_settings, "steam_demo_download_max_redirects")
        ),
        connect_timeout_seconds=float(
            getattr(
                runtime_settings,
                "steam_demo_download_connect_timeout_seconds",
            )
        ),
        read_timeout_seconds=float(
            getattr(runtime_settings, "steam_demo_download_read_timeout_seconds")
        ),
        total_timeout_seconds=float(
            getattr(runtime_settings, "steam_demo_download_total_timeout_seconds")
        ),
    )
    return SecureDemoDownloader(
        resolver=SystemAddressResolver(),
        transport=StdlibPinnedHttpsTransport(),
        policy=policy,
        scratch_root=scratch_root,
    )
