from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class DemoSource:
    provider_id: str
    url: str = field(repr=False)
    filename: str = "steam-match.dem"

    def __post_init__(self) -> None:
        if (
            not self.provider_id
            or len(self.provider_id) > 64
            or any(ord(character) < 32 for character in self.provider_id)
        ):
            raise ValueError("Demo source provider id is invalid")
        if not self.url or len(self.url) > 4096:
            raise ValueError("Demo source URL is invalid")
        if (
            not self.filename
            or len(self.filename) > 255
            or not self.filename.lower().endswith(".dem")
            or "/" in self.filename
            or "\\" in self.filename
            or any(ord(character) < 32 for character in self.filename)
        ):
            raise ValueError("Demo source filename is invalid")


class DemoSourceUnavailableError(RuntimeError):
    def __init__(self, code: str = "provider_not_configured"):
        self.code = code
        self.manual_upload_supported = True
        super().__init__(
            "Automatic Demo source is unavailable. Upload the .dem file manually."
        )


class DemoSourceProvider(Protocol):
    provider_id: str
    available: bool

    def resolve(self, *, share_code: str) -> DemoSource: ...


class DisabledDemoSourceProvider:
    provider_id = "disabled"
    available = False

    def resolve(self, *, share_code: str) -> None:
        del share_code
        raise DemoSourceUnavailableError()


def demo_source_provider_from_settings(runtime_settings: object) -> DemoSourceProvider:
    provider_id = str(
        getattr(runtime_settings, "steam_demo_provider", "disabled")
    ).strip().lower()
    experimental_enabled = bool(
        getattr(
            runtime_settings,
            "steam_demo_experimental_replay_cdn_enabled",
            False,
        )
    )
    if provider_id == "disabled" and not experimental_enabled:
        return DisabledDemoSourceProvider()
    raise RuntimeError(
        "No licensed automatic Demo source provider is registered in this build"
    )
