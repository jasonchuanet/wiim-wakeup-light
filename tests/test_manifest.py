from __future__ import annotations

import json
from pathlib import Path


def test_core_upnp_owns_async_upnp_client_requirement() -> None:
    """Avoid pinning a Core-constrained package from the custom integration."""
    manifest_path = (
        Path(__file__).parents[1]
        / "custom_components"
        / "wiim_wakeup_light"
        / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text())

    assert "upnp" in manifest["after_dependencies"]
    assert "requirements" not in manifest
