"""The available providers, by id (the source stored in a config entry)."""

from __future__ import annotations

from .api import NdwFeed
from .ocm import OcmClient
from .provider import Provider

PROVIDERS: dict[str, type[Provider]] = {
    provider.provider_id: provider for provider in (NdwFeed, OcmClient)
}
