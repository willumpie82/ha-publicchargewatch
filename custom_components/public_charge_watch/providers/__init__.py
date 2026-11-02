"""The available providers, by id (the source stored in a config entry)."""

from __future__ import annotations

from .base import Provider
from .ndw_api import NdwApi
from .ndw_download import NdwFeed
from .ocm import OcmClient

PROVIDERS: dict[str, type[Provider]] = {
    provider.provider_id: provider for provider in (NdwFeed, NdwApi, OcmClient)
}
