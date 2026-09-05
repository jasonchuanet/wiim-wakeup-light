from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from async_upnp_client.exceptions import UpnpError

from custom_components.wiim_wakeup_light.const import SUBSCRIPTION_RETRY_SECONDS
from custom_components.wiim_wakeup_light.upnp import (
    WiimWakeUpLightSubscription,
    _renewal_delay,
)


class FakeEntry:
    title = "Bedroom"

    def __init__(self) -> None:
        self.tasks: list[asyncio.Task[None]] = []

    def async_create_background_task(
        self, hass: Any, coro: Any, *, name: str
    ) -> asyncio.Task[None]:
        task = asyncio.create_task(coro, name=name)
        self.tasks.append(task)
        return task


class FakeEventHandler:
    def __init__(self) -> None:
        self.subscribe_calls = 0
        self.resubscribe_calls = 0
        self.unsubscribe_calls: list[str] = []
        self.fail_renewal = False

    async def async_subscribe(
        self, service: Any, *, timeout: timedelta
    ) -> tuple[str, timedelta]:
        self.subscribe_calls += 1
        return f"uuid:sid-{self.subscribe_calls}", timeout

    async def async_resubscribe(
        self, sid: str, *, timeout: timedelta
    ) -> tuple[str, timedelta]:
        self.resubscribe_calls += 1
        if self.fail_renewal:
            raise UpnpError("device rebooted")
        return sid, timeout

    async def async_unsubscribe(self, sid: str) -> None:
        self.unsubscribe_calls.append(sid)


class FakeManager:
    def __init__(self, handler: FakeEventHandler) -> None:
        self.requester = None
        self.lease = SimpleNamespace(
            source_ip="192.0.2.10",
            server=SimpleNamespace(event_handler=handler),
        )
        self.released = False

    async def async_acquire(self, host: str) -> Any:
        return self.lease

    async def async_release(self, lease: Any) -> None:
        self.released = True


@pytest.mark.asyncio
async def test_subscription_start_and_unload_cleanup() -> None:
    handler = FakeEventHandler()
    manager = FakeManager(handler)
    entry = FakeEntry()
    subscription = WiimWakeUpLightSubscription(
        SimpleNamespace(),
        entry,  # type: ignore[arg-type]
        manager,  # type: ignore[arg-type]
        "192.0.2.1",
        "http://192.0.2.1:49152/description.xml",
        lambda state: None,
    )
    service = SimpleNamespace(on_event=subscription._async_handle_event)
    subscription._service = service

    assert await subscription.async_start() is True
    assert handler.subscribe_calls == 1
    assert service.on_event is not None

    await subscription.async_stop()

    assert handler.unsubscribe_calls == ["uuid:sid-1"]
    assert manager.released is True
    assert service.on_event is None


@pytest.mark.asyncio
async def test_failed_renewal_drops_sid_then_resubscribes() -> None:
    handler = FakeEventHandler()
    manager = FakeManager(handler)
    subscription = WiimWakeUpLightSubscription(
        SimpleNamespace(),
        FakeEntry(),  # type: ignore[arg-type]
        manager,  # type: ignore[arg-type]
        "192.0.2.1",
        "http://192.0.2.1:49152/description.xml",
        lambda state: None,
    )
    subscription._service = SimpleNamespace(on_event=None)
    subscription._lease = manager.lease
    subscription._sid = "uuid:old"

    handler.fail_renewal = True
    assert await subscription._async_renew_once() == SUBSCRIPTION_RETRY_SECONDS
    assert subscription.active is False

    handler.fail_renewal = False
    assert await subscription._async_renew_once() == 240
    assert subscription.active is True
    assert handler.subscribe_calls == 1


@pytest.mark.parametrize(
    ("timeout", "expected"),
    [(300, 240), (30, 24), (5, 5)],
)
def test_renewal_happens_before_expiration(timeout: float, expected: float) -> None:
    assert _renewal_delay(timeout) == expected
