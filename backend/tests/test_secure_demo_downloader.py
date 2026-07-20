import hashlib
import queue
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import Settings
from app.services.demo_source_provider import DemoSource
from app.services.secure_demo_downloader import (
    DemoDownloadConfigurationError,
    DemoDownloadError,
    DemoDownloadPolicy,
    SecureDemoDownloader,
    StdlibPinnedHttpsTransport,
    SystemAddressResolver,
    _PinnedHTTPSConnection,
    secure_demo_downloader_from_settings,
)


PUBLIC_IP = "93.184.216.34"
DEMO_BYTES = b"HL2DEMO\x00" + (b"safe-demo-byte" * 8)


class FakeResolver:
    def __init__(self, *addresses: str, by_host: dict[str, tuple[str, ...]] | None = None):
        self.addresses = addresses
        self.by_host = by_host or {}
        self.calls: list[tuple[str, int]] = []

    def resolve(
        self,
        hostname: str,
        port: int,
        *,
        timeout_seconds: float,
    ) -> tuple[str, ...]:
        self.assert_timeout = timeout_seconds
        self.calls.append((hostname, port))
        return self.by_host.get(hostname, tuple(self.addresses))


class StepClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeStreamingResponse:
    def __init__(
        self,
        payload: bytes = b"",
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ):
        self.status_code = status_code
        self.headers = headers or {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(payload)),
            "Content-Encoding": "identity",
        }
        self.payload = payload
        self.iterated = False

    def iter_raw(self, chunk_size: int):
        self.iterated = True
        for offset in range(0, len(self.payload), chunk_size):
            yield self.payload[offset : offset + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        return None


class FakePinnedTransport:
    def __init__(self, *responses: FakeStreamingResponse):
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def open(self, **kwargs: object) -> FakeStreamingResponse:
        self.calls.append(dict(kwargs))
        if not self.responses:
            raise AssertionError("Unexpected download request")
        return self.responses.pop(0)


class InterruptingStreamingResponse(FakeStreamingResponse):
    def iter_raw(self, chunk_size: int):
        del chunk_size
        yield DEMO_BYTES[:24]
        raise ConnectionError("signed-url-secret")


class FakeHttpResponse:
    status = 200

    def __init__(self, payload: bytes):
        self.payload = payload
        self.offset = 0
        self.headers = {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(payload)),
            "Content-Encoding": "identity",
        }

    def read(self, size: int) -> bytes:
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk

    def close(self) -> None:
        return None


class FakePinnedConnection:
    instances: list["FakePinnedConnection"] = []

    def __init__(self, **kwargs: object):
        self.kwargs = dict(kwargs)
        self.request_call: tuple[str, str, dict[str, str]] | None = None
        self.closed = False
        self.__class__.instances.append(self)

    def request(self, method: str, target: str, *, headers: dict[str, str]) -> None:
        self.request_call = (method, target, dict(headers))

    def getresponse(self) -> FakeHttpResponse:
        return FakeHttpResponse(DEMO_BYTES)

    def close(self) -> None:
        self.closed = True


class SecureDemoDownloaderTest(unittest.TestCase):
    def _downloader(
        self,
        *,
        resolver: FakeResolver | None = None,
        transport: FakePinnedTransport | None = None,
        max_bytes: int = 1024,
        monotonic: object | None = None,
        max_redirects: int = 3,
    ) -> SecureDemoDownloader:
        kwargs: dict[str, object] = {}
        if monotonic is not None:
            kwargs["monotonic"] = monotonic
        return SecureDemoDownloader(
            resolver=resolver or FakeResolver(PUBLIC_IP),
            transport=transport or FakePinnedTransport(FakeStreamingResponse(DEMO_BYTES)),
            policy=DemoDownloadPolicy(
                exact_hosts=frozenset(
                    {"demos.licensed.example", "cdn.licensed.example"}
                ),
                max_bytes=max_bytes,
                max_redirects=max_redirects,
            ),
            **kwargs,
        )

    def test_download_pins_validated_ip_and_returns_private_seekable_scratch(self) -> None:
        resolver = FakeResolver(PUBLIC_IP)
        transport = FakePinnedTransport(FakeStreamingResponse(DEMO_BYTES))
        policy = DemoDownloadPolicy(
            exact_hosts=frozenset({"demos.licensed.example"}),
            max_bytes=1024,
        )
        source = DemoSource(
            provider_id="licensed-test",
            url="https://demos.licensed.example/match.dem?signature=secret",
            filename="steam-match.dem",
        )

        with tempfile.TemporaryDirectory() as scratch_root:
            downloader = SecureDemoDownloader(
                resolver=resolver,
                transport=transport,
                policy=policy,
                scratch_root=Path(scratch_root),
            )
            with downloader.download(source) as downloaded:
                scratch_path = downloaded.path
                self.assertTrue(scratch_path.exists())
                self.assertEqual(scratch_path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(downloaded.stream.read(), DEMO_BYTES)
                self.assertEqual(downloaded.size_bytes, len(DEMO_BYTES))
                self.assertEqual(
                    downloaded.sha256,
                    hashlib.sha256(DEMO_BYTES).hexdigest(),
                )
                self.assertEqual(downloaded.filename, "steam-match.dem")
                self.assertEqual(downloaded.content_type, "application/octet-stream")
            self.assertFalse(scratch_path.exists())

        self.assertEqual(resolver.calls, [("demos.licensed.example", 443)])
        self.assertEqual(len(transport.calls), 1)
        request = transport.calls[0]
        self.assertEqual(request["connect_ip"], PUBLIC_IP)
        self.assertEqual(request["tls_server_name"], "demos.licensed.example")
        self.assertEqual(request["host_header"], "demos.licensed.example")
        self.assertNotIn("secret", repr(source))

    def test_dns_and_transport_share_one_absolute_monotonic_deadline(self) -> None:
        clock = StepClock()

        class AdvancingResolver(FakeResolver):
            def resolve(
                self,
                hostname: str,
                port: int,
                *,
                timeout_seconds: float,
            ) -> tuple[str, ...]:
                result = super().resolve(
                    hostname,
                    port,
                    timeout_seconds=timeout_seconds,
                )
                clock.advance(2.0)
                return result

        resolver = AdvancingResolver(PUBLIC_IP)
        transport = FakePinnedTransport(FakeStreamingResponse(DEMO_BYTES))
        downloader = SecureDemoDownloader(
            resolver=resolver,
            transport=transport,
            policy=DemoDownloadPolicy(
                exact_hosts=frozenset({"demos.licensed.example"}),
                max_bytes=1024,
                connect_timeout_seconds=3.0,
                read_timeout_seconds=3.0,
                total_timeout_seconds=5.0,
            ),
            monotonic=clock,
        )

        with downloader.download(
            DemoSource(
                provider_id="licensed-test",
                url="https://demos.licensed.example/match.dem",
            )
        ) as downloaded:
            self.assertEqual(downloaded.stream.read(), DEMO_BYTES)

        self.assertEqual(resolver.assert_timeout, 3.0)
        self.assertEqual(transport.calls[0]["deadline_at"], 5.0)
        self.assertIs(transport.calls[0]["monotonic"], clock)

    def test_redirect_to_metadata_address_is_rejected_before_second_connect(self) -> None:
        resolver = FakeResolver(
            by_host={
                "demos.licensed.example": (PUBLIC_IP,),
                "cdn.licensed.example": ("169.254.169.254",),
            }
        )
        transport = FakePinnedTransport(
            FakeStreamingResponse(
                status_code=302,
                headers={"Location": "https://cdn.licensed.example/match.dem"},
            )
        )
        source = DemoSource(
            provider_id="licensed-test",
            url="https://demos.licensed.example/start",
        )
        downloader = SecureDemoDownloader(
            resolver=resolver,
            transport=transport,
            policy=DemoDownloadPolicy(
                exact_hosts=frozenset(
                    {"demos.licensed.example", "cdn.licensed.example"}
                ),
                max_bytes=1024,
            ),
        )

        with self.assertRaises(DemoDownloadError) as raised:
            with downloader.download(source):
                self.fail("metadata redirect must not yield a demo")

        self.assertEqual(raised.exception.code, "demo_source_rejected")
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(
            resolver.calls,
            [
                ("demos.licensed.example", 443),
                ("cdn.licensed.example", 443),
            ],
        )

    def test_stdlib_transport_uses_pinned_ip_with_original_tls_and_host(self) -> None:
        FakePinnedConnection.instances.clear()
        transport = StdlibPinnedHttpsTransport(
            connection_factory=FakePinnedConnection,
        )

        with transport.open(
            logical_url="https://demos.licensed.example/path/match.dem?signature=secret",
            connect_ip=PUBLIC_IP,
            tls_server_name="demos.licensed.example",
            host_header="demos.licensed.example",
            connect_timeout_seconds=3.0,
            read_timeout_seconds=7.0,
            deadline_at=30.0,
            monotonic=lambda: 0.0,
        ) as response:
            self.assertEqual(b"".join(response.iter_raw(32)), DEMO_BYTES)

        self.assertEqual(len(FakePinnedConnection.instances), 1)
        connection = FakePinnedConnection.instances[0]
        self.assertEqual(connection.kwargs["connect_ip"], PUBLIC_IP)
        self.assertEqual(
            connection.kwargs["tls_server_name"],
            "demos.licensed.example",
        )
        self.assertEqual(
            connection.request_call,
            (
                "GET",
                "/path/match.dem?signature=secret",
                {
                    "Accept": "application/octet-stream",
                    "Accept-Encoding": "identity",
                    "Host": "demos.licensed.example",
                    "User-Agent": "CS2DemoCoach/1.0",
                },
            ),
        )
        self.assertTrue(connection.closed)

    def test_cumulative_header_stages_cannot_reset_absolute_deadline(self) -> None:
        clock = StepClock()

        class CumulativeStageConnection(FakePinnedConnection):
            def request(
                self,
                method: str,
                target: str,
                *,
                headers: dict[str, str],
            ) -> None:
                super().request(method, target, headers=headers)
                clock.advance(2.0)

            def getresponse(self) -> FakeHttpResponse:
                clock.advance(2.0)
                return super().getresponse()

        transport = StdlibPinnedHttpsTransport(
            connection_factory=CumulativeStageConnection,
        )

        with self.assertRaises(DemoDownloadError) as raised:
            with transport.open(
                logical_url="https://demos.licensed.example/match.dem",
                connect_ip=PUBLIC_IP,
                tls_server_name="demos.licensed.example",
                host_header="demos.licensed.example",
                connect_timeout_seconds=3.0,
                read_timeout_seconds=3.0,
                deadline_at=3.0,
                monotonic=clock,
            ):
                self.fail("cumulative header time must exhaust the shared deadline")

        self.assertEqual(raised.exception.code, "demo_download_timeout")
        self.assertTrue(CumulativeStageConnection.instances[-1].closed)

    def test_tcp_and_tls_timeouts_shrink_with_cumulative_deadline(self) -> None:
        clock = StepClock()
        test_case = self

        class TimedSocket:
            def __init__(self) -> None:
                self.timeouts: list[float] = []
                self.closed = False

            def getpeername(self) -> tuple[str, int]:
                return (PUBLIC_IP, 443)

            def settimeout(self, seconds: float) -> None:
                self.timeouts.append(seconds)

            def close(self) -> None:
                self.closed = True

        raw_socket = TimedSocket()
        tls_socket = TimedSocket()
        tcp_timeouts: list[float] = []

        def create_connection(
            address: tuple[str, int],
            *,
            timeout: float,
        ) -> TimedSocket:
            self.assertEqual(address, (PUBLIC_IP, 443))
            tcp_timeouts.append(timeout)
            clock.advance(2.5)
            return raw_socket

        class TimedTlsContext:
            check_hostname = False
            verify_mode = 0

            def wrap_socket(
                self,
                sock: TimedSocket,
                *,
                server_hostname: str,
            ) -> TimedSocket:
                test_case.assertIs(sock, raw_socket)
                test_case.assertEqual(
                    server_hostname,
                    "demos.licensed.example",
                )
                clock.advance(1.5)
                return tls_socket

        connection = _PinnedHTTPSConnection(
            connect_ip=PUBLIC_IP,
            tls_server_name="demos.licensed.example",
            connect_timeout_seconds=3.0,
            read_timeout_seconds=3.0,
            deadline_at=5.0,
            monotonic=clock,
            ssl_context=TimedTlsContext(),
        )

        with patch(
            "app.services.secure_demo_downloader.socket.create_connection",
            side_effect=create_connection,
        ):
            connection.connect()

        self.assertEqual(tcp_timeouts, [3.0])
        self.assertEqual(raw_socket.timeouts, [2.5])
        self.assertEqual(tls_socket.timeouts, [1.0])
        connection.close()

    def test_blocked_body_read_uses_read1_and_maps_timeout_stably(self) -> None:
        class BlockingHttpResponse(FakeHttpResponse):
            def __init__(self) -> None:
                super().__init__(DEMO_BYTES)
                self.read_calls = 0
                self.read1_calls = 0

            def read(self, size: int) -> bytes:
                self.read_calls += 1
                return super().read(size)

            def read1(self, size: int) -> bytes:
                del size
                self.read1_calls += 1
                raise socket.timeout("signed-url-secret")

        blocked_response = BlockingHttpResponse()

        class BlockingBodyConnection(FakePinnedConnection):
            def getresponse(self) -> BlockingHttpResponse:
                return blocked_response

        transport = StdlibPinnedHttpsTransport(
            connection_factory=BlockingBodyConnection,
        )

        with self.assertRaises(DemoDownloadError) as raised:
            with transport.open(
                logical_url="https://demos.licensed.example/match.dem",
                connect_ip=PUBLIC_IP,
                tls_server_name="demos.licensed.example",
                host_header="demos.licensed.example",
                connect_timeout_seconds=3.0,
                read_timeout_seconds=3.0,
                deadline_at=5.0,
                monotonic=lambda: 0.0,
            ) as response:
                tuple(response.iter_raw(32))

        self.assertEqual(raised.exception.code, "demo_download_timeout")
        self.assertNotIn("secret", str(raised.exception))
        self.assertEqual(blocked_response.read1_calls, 1)
        self.assertEqual(blocked_response.read_calls, 0)

    def test_fsync_cannot_finish_after_the_absolute_deadline(self) -> None:
        clock = StepClock()
        downloader = SecureDemoDownloader(
            resolver=FakeResolver(PUBLIC_IP),
            transport=FakePinnedTransport(FakeStreamingResponse(DEMO_BYTES)),
            policy=DemoDownloadPolicy(
                exact_hosts=frozenset({"demos.licensed.example"}),
                max_bytes=1024,
                connect_timeout_seconds=3.0,
                read_timeout_seconds=3.0,
                total_timeout_seconds=5.0,
            ),
            monotonic=clock,
        )

        def delayed_fsync(_fd: int) -> None:
            clock.advance(6.0)

        with patch(
            "app.services.secure_demo_downloader.os.fsync",
            side_effect=delayed_fsync,
        ):
            with self.assertRaises(DemoDownloadError) as raised:
                with downloader.download(
                    DemoSource(
                        provider_id="licensed-test",
                        url="https://demos.licensed.example/match.dem",
                    )
                ):
                    pass

        self.assertEqual(raised.exception.code, "demo_download_timeout")

    def test_interrupted_download_returns_safe_error_and_removes_scratch(self) -> None:
        source = DemoSource(
            provider_id="licensed-test",
            url="https://demos.licensed.example/match.dem?signature=secret",
        )
        with tempfile.TemporaryDirectory() as scratch_root:
            downloader = SecureDemoDownloader(
                resolver=FakeResolver(PUBLIC_IP),
                transport=FakePinnedTransport(InterruptingStreamingResponse()),
                policy=DemoDownloadPolicy(
                    exact_hosts=frozenset({"demos.licensed.example"}),
                    max_bytes=1024,
                ),
                scratch_root=Path(scratch_root),
            )

            with self.assertRaises(DemoDownloadError) as raised:
                with downloader.download(source):
                    self.fail("interrupted download must not yield a demo")

            self.assertEqual(raised.exception.code, "demo_download_interrupted")
            self.assertNotIn("secret", str(raised.exception))
            self.assertEqual(list(Path(scratch_root).iterdir()), [])

    def test_rejects_unsafe_urls_before_dns_or_network(self) -> None:
        unsafe_urls = (
            "http://demos.licensed.example/match.dem",
            "https://user:secret@demos.licensed.example/match.dem",
            "https://93.184.216.34/match.dem",
            "https://demos.licensed.example.evil.test/match.dem",
            "https://demos.licensed.example:444/match.dem",
            "https://demos.licensed.example\\@evil.test/match.dem",
            "https://demos.licensed.example/match.dem#fragment",
            "https://demos.licensed.example/match.dem\nignored",
        )
        for url in unsafe_urls:
            with self.subTest(url=url):
                resolver = FakeResolver(PUBLIC_IP)
                transport = FakePinnedTransport()
                downloader = self._downloader(
                    resolver=resolver,
                    transport=transport,
                )
                with self.assertRaises(DemoDownloadError) as raised:
                    with downloader.download(
                        DemoSource(provider_id="licensed-test", url=url)
                    ):
                        self.fail("unsafe URL must not yield")
                self.assertEqual(raised.exception.code, "demo_source_rejected")
                self.assertEqual(resolver.calls, [])
                self.assertEqual(transport.calls, [])

    def test_rejects_private_metadata_and_mixed_dns_answers_before_connect(self) -> None:
        unsafe_answers = (
            ("127.0.0.1",),
            ("10.0.0.1",),
            ("169.254.169.254",),
            ("100.100.100.200",),
            ("100.64.0.1",),
            ("224.0.0.1",),
            ("::1",),
            ("fec0::1",),
            ("ff02::1",),
            ("64:ff9b::808:808",),
            (PUBLIC_IP, "10.0.0.1"),
        )
        for addresses in unsafe_answers:
            with self.subTest(addresses=addresses):
                transport = FakePinnedTransport()
                downloader = self._downloader(
                    resolver=FakeResolver(*addresses),
                    transport=transport,
                )
                with self.assertRaises(DemoDownloadError):
                    with downloader.download(
                        DemoSource(
                            provider_id="licensed-test",
                            url="https://demos.licensed.example/match.dem",
                        )
                    ):
                        self.fail("unsafe DNS answer must not yield")
                self.assertEqual(transport.calls, [])

    def test_dns_rebinding_cannot_replace_the_validated_numeric_connect_target(self) -> None:
        class RebindingResolver(FakeResolver):
            def resolve(
                self,
                hostname: str,
                port: int,
                *,
                timeout_seconds: float,
            ) -> tuple[str, ...]:
                self.assert_timeout = timeout_seconds
                self.calls.append((hostname, port))
                return (PUBLIC_IP,) if len(self.calls) == 1 else ("127.0.0.1",)

        resolver = RebindingResolver()
        transport = FakePinnedTransport(FakeStreamingResponse(DEMO_BYTES))
        downloader = self._downloader(resolver=resolver, transport=transport)

        with downloader.download(
            DemoSource(
                provider_id="licensed-test",
                url="https://demos.licensed.example/match.dem",
            )
        ) as downloaded:
            self.assertEqual(downloaded.stream.read(), DEMO_BYTES)

        self.assertEqual(resolver.calls, [("demos.licensed.example", 443)])
        self.assertEqual(transport.calls[0]["connect_ip"], PUBLIC_IP)
        self.assertNotEqual(
            transport.calls[0]["connect_ip"],
            "demos.licensed.example",
        )

    def test_redirect_revalidates_scheme_host_dns_and_limit(self) -> None:
        cases = (
            ("http://demos.licensed.example/match.dem", 3),
            ("https://evil.test/match.dem", 3),
            ("/loop", 0),
        )
        for location, max_redirects in cases:
            with self.subTest(location=location):
                redirect = FakeStreamingResponse(
                    status_code=302,
                    headers={"Location": location},
                )
                transport = FakePinnedTransport(redirect)
                downloader = self._downloader(
                    transport=transport,
                    max_redirects=max_redirects,
                )
                with self.assertRaises(DemoDownloadError):
                    with downloader.download(
                        DemoSource(
                            provider_id="licensed-test",
                            url="https://demos.licensed.example/start",
                        )
                    ):
                        self.fail("malicious redirect must not yield")
                self.assertEqual(len(transport.calls), 1)

    def test_rejects_oversize_and_compressed_responses_before_body_decode(self) -> None:
        oversized = FakeStreamingResponse(
            DEMO_BYTES,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": "4096",
                "Content-Encoding": "identity",
            },
        )
        compressed = FakeStreamingResponse(
            b"gzip-bomb",
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": "9",
                "Content-Encoding": "gzip",
            },
        )
        for response, expected_code in (
            (oversized, "demo_download_too_large"),
            (compressed, "demo_download_compression_rejected"),
        ):
            with self.subTest(expected_code=expected_code):
                downloader = self._downloader(
                    transport=FakePinnedTransport(response),
                    max_bytes=1024,
                )
                with self.assertRaises(DemoDownloadError) as raised:
                    with downloader.download(
                        DemoSource(
                            provider_id="licensed-test",
                            url="https://demos.licensed.example/match.dem",
                        )
                    ):
                        self.fail("rejected body must not yield")
                self.assertEqual(raised.exception.code, expected_code)
                self.assertFalse(response.iterated)

        ambiguous = FakeStreamingResponse(
            DEMO_BYTES,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(DEMO_BYTES)),
                "Content-Encoding": "identity",
                "Transfer-Encoding": "chunked",
            },
        )
        with self.assertRaises(DemoDownloadError) as raised:
            with self._downloader(
                transport=FakePinnedTransport(ambiguous)
            ).download(
                DemoSource(
                    provider_id="licensed-test",
                    url="https://demos.licensed.example/match.dem",
                )
            ):
                self.fail("ambiguous framing must not yield")
        self.assertEqual(raised.exception.code, "demo_download_headers_rejected")
        self.assertFalse(ambiguous.iterated)

    def test_enforces_stream_limit_length_and_absolute_deadline(self) -> None:
        stream_too_large = FakeStreamingResponse(
            b"x" * 65,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Encoding": "identity",
            },
        )
        downloader = self._downloader(
            transport=FakePinnedTransport(stream_too_large),
            max_bytes=64,
        )
        with self.assertRaises(DemoDownloadError) as raised:
            with downloader.download(
                DemoSource(
                    provider_id="licensed-test",
                    url="https://demos.licensed.example/match.dem",
                )
            ):
                self.fail("oversize stream must not yield")
        self.assertEqual(raised.exception.code, "demo_download_too_large")

        short = FakeStreamingResponse(
            DEMO_BYTES,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(DEMO_BYTES) + 1),
                "Content-Encoding": "identity",
            },
        )
        with self.assertRaises(DemoDownloadError) as raised:
            with self._downloader(
                transport=FakePinnedTransport(short)
            ).download(
                DemoSource(
                    provider_id="licensed-test",
                    url="https://demos.licensed.example/match.dem",
                )
            ):
                self.fail("length mismatch must not yield")
        self.assertEqual(raised.exception.code, "demo_download_truncated")

        times = iter((0.0, 0.0, 31.0))
        timed = self._downloader(
            transport=FakePinnedTransport(FakeStreamingResponse(DEMO_BYTES)),
            monotonic=lambda: next(times),
        )
        with self.assertRaises(DemoDownloadError) as raised:
            with timed.download(
                DemoSource(
                    provider_id="licensed-test",
                    url="https://demos.licensed.example/match.dem",
                )
            ):
                self.fail("deadline must not yield")
        self.assertEqual(raised.exception.code, "demo_download_timeout")

    def test_settings_factory_requires_exact_hosts_and_copies_all_limits(self) -> None:
        with self.assertRaises(DemoDownloadConfigurationError):
            secure_demo_downloader_from_settings(Settings(auth_mode="test"))

        configured = Settings(
            auth_mode="test",
            steam_demo_download_allowed_hosts_raw="demos.licensed.example",
            steam_demo_download_max_bytes=4096,
            steam_demo_download_max_redirects=2,
            steam_demo_download_connect_timeout_seconds=2,
            steam_demo_download_read_timeout_seconds=3,
            steam_demo_download_total_timeout_seconds=5,
        )
        downloader = secure_demo_downloader_from_settings(configured)
        self.assertEqual(
            downloader.policy.exact_hosts,
            frozenset({"demos.licensed.example"}),
        )
        self.assertEqual(downloader.policy.max_bytes, 4096)
        self.assertEqual(downloader.policy.max_redirects, 2)
        self.assertEqual(downloader.policy.connect_timeout_seconds, 2)
        self.assertEqual(downloader.policy.read_timeout_seconds, 3)
        self.assertEqual(downloader.policy.total_timeout_seconds, 5)

    def test_system_dns_resolution_has_a_bounded_fail_closed_timeout(self) -> None:
        release_lookup = threading.Event()
        observed_timeouts: list[float | None] = []
        queue_get = queue.Queue.get

        def blocked_lookup(*_args: object, **_kwargs: object) -> list[object]:
            release_lookup.wait(timeout=1)
            return []

        def recording_get(
            result_queue: queue.Queue[object],
            block: bool = True,
            timeout: float | None = None,
        ) -> object:
            observed_timeouts.append(timeout)
            return queue_get(result_queue, block=block, timeout=timeout)

        try:
            with patch(
                "app.services.secure_demo_downloader.socket.getaddrinfo",
                side_effect=blocked_lookup,
            ), patch(
                "app.services.secure_demo_downloader.queue.Queue.get",
                new=recording_get,
            ):
                with self.assertRaises(DemoDownloadError) as raised:
                    SystemAddressResolver().resolve(
                        "demos.licensed.example",
                        443,
                        timeout_seconds=0.01,
                    )
            self.assertEqual(raised.exception.code, "demo_download_timeout")
            self.assertEqual(observed_timeouts, [0.01])
        finally:
            release_lookup.set()


if __name__ == "__main__":
    unittest.main()
