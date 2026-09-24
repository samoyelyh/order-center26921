from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.material_platform import (
    FakeMaterialPlatformGateway,
    MaterialPlatformUnavailableError,
    get_material_platform,
)


def test_development_may_use_fake_when_explicitly_allowed(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "material_platform_base_url", "")
    monkeypatch.setattr(settings, "material_platform_allow_fake", True)
    assert isinstance(get_material_platform(), FakeMaterialPlatformGateway)


def test_production_never_silently_falls_back_to_fake(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "material_platform_base_url", "")
    monkeypatch.setattr(settings, "material_platform_allow_fake", True)
    with pytest.raises(MaterialPlatformUnavailableError, match="MATERIAL_PLATFORM_BASE_URL"):
        get_material_platform()


def test_fake_can_be_disabled_in_development(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "material_platform_base_url", "")
    monkeypatch.setattr(settings, "material_platform_allow_fake", False)
    with pytest.raises(MaterialPlatformUnavailableError):
        get_material_platform()
