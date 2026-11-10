# SPDX-License-Identifier: GPL-3.0-or-later
"""Low video delay: play a little faster while more is buffered than needed."""
from multichat.video import catch_up as cu
from multichat.video.catch_up import CatchUp


class Clock:
    def __init__(self):
        self.now = 500.0

    def __call__(self):
        return self.now


def run(ctl, clock, seconds, buffer, step=0.25):
    """Feeds samples like mpv does (about four a second) and polls every two seconds.
    buffer: seconds buffered, or a function of the time. → the speeds that were set."""
    changes, t, next_poll = [], 0.0, 2.0
    while t < seconds:
        clock.now += step
        t += step
        ctl.sample(buffer(t) if callable(buffer) else buffer)
        if t >= next_poll:
            next_poll += 2.0
            speed = ctl.poll()
            if speed is not None:
                changes.append(speed)
    return changes


def test_nothing_changes_while_the_buffer_is_as_small_as_it_should_be():
    clock = Clock()
    ctl = CatchUp(clock)
    assert run(ctl, clock, 60, cu.RESERVE + 1.0) == [] and ctl.speed == 1.0


def test_plays_faster_until_the_extra_buffer_is_gone():
    clock = Clock()
    ctl = CatchUp(clock)
    buffered = [7.0]  # five seconds more than the reserve

    def buffer(_t):
        buffered[0] -= (ctl.speed - 1.0) * 0.25  # what playing faster eats per sample
        return buffered[0]

    changes = run(ctl, clock, 120, buffer)
    assert changes[0] == 1.10 and changes[-1] == 1.0  # faster, then back to normal
    assert 1.05 in changes  # more gently for the last bit
    assert cu.RESERVE - 0.3 <= buffered[0] <= cu.RESERVE + 0.1 and ctl.speed == 1.0
    assert run(ctl, clock, 60, buffered[0]) == []  # and it stays there


def test_far_behind_catches_up_faster():
    clock = Clock()
    ctl = CatchUp(clock)
    assert run(ctl, clock, 14, 20.0) == [1.25]


def test_the_smallest_buffer_counts_not_the_one_right_after_a_segment():
    """HLS: a five-second segment arrives at once. Right after it the buffer is full, just
    before the next one nearly empty – going by a single reading would drain it."""
    clock = Clock()
    ctl = CatchUp(clock)

    def sawtooth(t):
        return 1.5 + 5.0 - (t % 5.0)  # between 6.5 s (segment just arrived) and 1.5 s

    assert run(ctl, clock, 90, sawtooth) == [] and ctl.speed == 1.0
    # the same swing with four seconds more at every moment: that part can go
    assert run(ctl, clock, 20, lambda t: sawtooth(t) + 4.0)[0] == 1.10


def test_waits_for_a_whole_window_and_starts_again_after_a_reload_or_a_stall():
    clock = Clock()
    ctl = CatchUp(clock)
    assert run(ctl, clock, cu.WINDOW - 2, 30.0) == []  # too early to judge
    assert run(ctl, clock, 4, 30.0) == [1.25]
    ctl.reset()  # the video waited for data (or was loaded again)
    assert ctl.speed == 1.0
    assert run(ctl, clock, cu.WINDOW - 2, 30.0) == []
    ctl.sample(None)  # mpv has no value yet
    ctl.sample(-1)
    assert ctl.poll() is None


def test_twitch_is_opened_in_low_latency_mode_only_when_asked(monkeypatch):
    """resolve() passes the setting on; Kick and YouTube get their option for mpv."""
    from multichat.video import streams

    seen = []
    monkeypatch.setattr(streams, "_twitch", lambda channel, max_h, low_latency=False:
                        seen.append(low_latency) or streams.Stream("fdclose://7", pipe_fd=7))
    streams.resolve("twitch", "examplechan", "720p", True)
    streams.resolve("twitch", "examplechan", "720p")
    assert seen == [True, False]
    assert streams.LOW_LATENCY_MPV == {"demuxer-lavf-o": "live_start_index=-2"}


def test_setting_is_kept_and_defaults_to_on(tmp_path, monkeypatch):
    import json

    from multichat.core import config

    monkeypatch.setattr(config, "CONFIG", tmp_path / "config.json")
    assert config.load_config()["video"]["low_latency"] is True  # no file yet
    for stored, expected in ((False, False), (True, True), ("no", True)):  # "no" is not a switch value
        (tmp_path / "config.json").write_text(json.dumps({"video": {"low_latency": stored}}))
        assert config.load_config()["video"]["low_latency"] is expected
