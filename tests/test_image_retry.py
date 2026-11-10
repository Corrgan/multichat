# SPDX-License-Identifier: GPL-3.0-or-later
"""Image recovery honors the earliest deadline without unbounded queued retries."""
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt

from multichat.ui import chat_view


@pytest.fixture
def view(monkeypatch):
    monkeypatch.setattr(chat_view, "time", SimpleNamespace(monotonic=lambda: 100.0))
    widget = chat_view.ChatView()
    # The production coarse timer may round its wake-up by 5%. Keep wall-clock
    # rounding out of these tests of deadlines computed from a fixed fake clock.
    widget._image_retry.setTimerType(Qt.TimerType.PreciseTimer)
    yield widget
    widget.shutdown()
    widget.close()


def test_new_image_failure_advances_an_existing_long_backoff(view):
    old, new = "https://cdn.invalid/old.png", "https://cdn.invalid/new.png"
    view._attempts[old] = 5
    view._failed_image(old)
    assert view._image_retry.interval() == 600_000

    view._failed_image(new)

    assert view.failed[new] == 130.0
    assert view._image_retry.interval() == 30_000
    assert 0 < view._image_retry.remainingTime() <= 31_000


def test_later_failure_does_not_postpone_an_earlier_retry(view):
    old, new = "https://cdn.invalid/old.png", "https://cdn.invalid/new.png"
    view._failed_image(old)
    timer_id = view._image_retry.timerId()
    view._attempts[new] = 5

    view._failed_image(new)

    assert view._image_retry.timerId() == timer_id
    assert view._image_retry.interval() == 30_000


@pytest.mark.parametrize("failed", [{}, {"https://cdn.invalid/active.png": chat_view.RETRYING}])
def test_no_timer_remains_when_only_inflight_or_no_failed_images_remain(view, failed):
    view._failed_image("https://cdn.invalid/old.png")
    view.failed.clear()
    view.failed.update(failed)

    view._schedule_image_retry()

    assert not view._image_retry.isActive()


def test_expired_retries_wait_for_batch_capacity_without_a_busy_timer(view, monkeypatch):
    monkeypatch.setattr(chat_view, "RETRY_BATCH", 2)
    requested = []
    monkeypatch.setattr(view._images, "request", lambda key: requested.append(key) or True)
    view.pending.update({"https://slow.invalid/1", "https://slow.invalid/2"})
    view.failed.update({"https://cdn.invalid/retry.png": 99.0,
                        "https://cdn.invalid/inflight.png": chat_view.RETRYING})

    view._retry_images()

    assert requested == []
    assert view._image_retry.interval() >= 100
    view.pending.clear()
    view._retry_images()
    assert requested == ["https://cdn.invalid/retry.png"]
    assert not view._image_retry.isActive()  # all failed images now have a request underway


def test_shutdown_cannot_reschedule_image_retries(view):
    view._failed_image("https://cdn.invalid/old.png")
    view.shutdown()

    view._failed_image("https://cdn.invalid/new.png")
    view._schedule_image_retry()
    view._retry_images()

    assert not view._image_retry.isActive()
    assert not view.failed and not view.pending
