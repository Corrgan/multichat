# SPDX-License-Identifier: GPL-3.0-or-later
"""Quality "Automatic": down when the video keeps stalling, back up when it runs smoothly."""
from multichat.video import auto_quality as aq
from multichat.video import streams
from multichat.video.auto_quality import AutoQuality

TWITCH = (160, 360, 480, 720, 1080)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def tick(self, seconds):
        self.now += seconds


def make(heights=TWITCH):
    clock = Clock()
    auto = AutoQuality(clock)
    auto.loaded(heights)
    return auto, clock


def stall(auto, clock, seconds=1.0):
    auto.stall(True)
    clock.tick(seconds)
    auto.stall(False)


def test_starts_at_720p_or_lower_for_a_small_tile():
    assert AutoQuality().level == "720p"
    auto = AutoQuality()
    auto.reset(tile_height=300)
    assert auto.level == "360p"
    auto.reset(tile_height=450)
    assert auto.level == "480p"
    auto.reset(tile_height=1000)
    assert auto.level == "720p"  # never starts above 720p; it climbs if the line carries it


def test_two_stalls_in_half_a_minute_step_down():
    auto, clock = make()
    clock.tick(30)
    stall(auto, clock)
    assert auto.poll(tile_height=1080) is None  # one hiccup is nothing
    clock.tick(10)
    stall(auto, clock)
    assert auto.poll(tile_height=1080) == "480p" and "720p → 480p" in auto.reason
    auto.loaded(TWITCH)
    clock.tick(30)
    stall(auto, clock)
    clock.tick(5)
    stall(auto, clock)
    assert auto.poll(tile_height=1080) == "360p"
    auto.loaded(TWITCH)
    clock.tick(30)
    stall(auto, clock)
    clock.tick(5)
    stall(auto, clock)
    assert auto.poll(tile_height=1080) is None and auto.level == "360p"  # the lowest level: stays


def test_one_long_stall_steps_down():
    auto, clock = make()
    clock.tick(40)
    auto.stall(True)
    clock.tick(3)
    assert auto.poll(tile_height=1080) is None
    clock.tick(3)
    assert auto.poll(tile_height=1080) == "480p"


def test_stalls_while_loading_and_during_ad_breaks_do_not_count():
    auto, clock = make()
    clock.tick(2)
    stall(auto, clock)  # buffering right after the start
    clock.tick(3)
    stall(auto, clock)
    clock.tick(30)
    assert auto.poll(tile_height=1080) is None and auto.level == "720p"
    # Twitch ad break: the ad filter leaves a gap, the player waits for data
    auto.stall(True)
    clock.tick(8)
    assert auto.poll(ads_active=True, tile_height=1080) is None
    auto.stall(False)
    clock.tick(5)
    stall(auto, clock)  # the stream catching up right after the break
    stall(auto, clock)
    assert auto.poll(tile_height=1080) is None and auto.level == "720p"


def test_steps_up_after_three_smooth_minutes_and_only_as_far_as_the_tile_shows():
    auto, clock = make()
    clock.tick(aq.UP_AFTER - 5)
    assert auto.poll(tile_height=1080) is None
    clock.tick(10)
    assert auto.poll(tile_height=600) is None  # 720p already fills a 600 px tile
    assert auto.poll(tile_height=0) is None  # hidden tile: nobody watches
    assert auto.poll(tile_height=1080) == "source" and "720p → source" in auto.reason
    auto.loaded(TWITCH)
    clock.tick(3600)
    assert auto.poll(tile_height=2160) is None  # nothing above the source


def test_failed_step_up_waits_longer_each_time():
    auto, clock = make()
    waits = []
    for _attempt in range(4):
        start = clock.now
        while auto.poll(tile_height=1080) is None:  # until it tries the source again
            clock.tick(2)
        waits.append(round(clock.now - start))
        assert auto.level == "source"
        auto.loaded(TWITCH)
        clock.tick(30)
        stall(auto, clock)
        clock.tick(5)
        stall(auto, clock)
        assert auto.poll(tile_height=1080) == "720p"  # too much for the line
        auto.loaded(TWITCH)
    assert waits[0] in range(180, 186) and waits[1] > waits[0] * 1.9 and waits[2] > waits[1] * 1.9
    assert waits[3] <= aq.UP_AFTER_MAX + 5  # at most every half hour
    # a step up that holds resets the patience
    while auto.poll(tile_height=1080) is None:
        clock.tick(2)
    auto.loaded(TWITCH)
    clock.tick(aq.PROBE_SECONDS + 5)
    assert auto.poll(tile_height=2160) is None and auto._up_wait == aq.UP_AFTER


def test_levels_that_give_the_same_picture_are_skipped():
    auto, clock = make(heights=(480, 720))  # the stream has no 360p and nothing above 720p
    clock.tick(3600)
    assert auto.poll(tile_height=2160) is None and auto.level == "720p"  # "source" would be the same 720p
    stall(auto, clock)
    clock.tick(5)
    stall(auto, clock)
    assert auto.poll(tile_height=2160) == "480p"
    auto.loaded((480, 720))
    clock.tick(30)
    stall(auto, clock)
    clock.tick(5)
    stall(auto, clock)
    assert auto.poll(tile_height=2160) is None and auto.level == "480p"  # "360p" would be the same 480p


def test_unknown_variants_are_learned_from_what_is_played():
    """YouTube: the variants are not known beforehand – a step that changes nothing is
    noticed from the played height and not tried again."""
    auto, clock = make(heights=())
    clock.tick(aq.HEIGHT_SETTLE + 1)
    assert auto.poll(played_height=720, tile_height=2160) is None
    clock.tick(aq.UP_AFTER)
    assert auto.poll(played_height=720, tile_height=2160) == "source"
    auto.loaded(())
    clock.tick(aq.HEIGHT_SETTLE + 1)
    assert auto.poll(played_height=720, tile_height=2160) is None
    assert auto.level == "720p"  # the source is 720p as well: back to the level's name, no reload
    clock.tick(7200)
    assert auto.poll(played_height=720, tile_height=2160) is None  # and not tried again


def test_stall_that_started_during_loading_counts_after_the_grace():
    auto, clock = make()
    clock.tick(2)
    auto.stall(True)
    clock.tick(aq.STARTUP_GRACE - 2)
    assert auto.poll(tile_height=1080) is None  # no time outside the grace yet
    clock.tick(118 - aq.STARTUP_GRACE + 2)
    assert auto.poll(tile_height=1080) == "480p"


def test_stall_crossing_the_grace_only_counts_its_unprotected_time():
    auto, clock = make()
    clock.tick(2)
    auto.stall(True)
    clock.tick(5)
    auto.poll(ads_active=True, tile_height=1080)
    clock.tick(aq.AD_GRACE + aq.LONG_STALL - 1)
    assert auto.poll(tile_height=1080) is None  # four seconds of actual trouble
    clock.tick(1)
    assert auto.poll(tile_height=1080) == "480p"


def test_ad_break_retains_an_open_stall_until_the_player_recovers():
    auto, clock = make()
    clock.tick(30)
    auto.stall(True)
    assert auto.poll(ads_active=True, played_height=720, tile_height=1080) is None
    clock.tick(aq.UP_AFTER + 1)
    assert auto.poll(played_height=720, tile_height=1080) == "480p"


def test_lowest_quality_keeps_a_stall_and_only_climbs_after_recovery():
    auto, clock = make()
    auto.reset(tile_height=300)
    auto.loaded(TWITCH)
    clock.tick(30)
    auto.stall(True)
    clock.tick(aq.LONG_STALL + 1)
    assert auto.poll(played_height=360, tile_height=1080) is None
    clock.tick(aq.UP_AFTER + 1)
    assert auto.poll(played_height=360, tile_height=1080) is None
    auto.stall(False)
    clock.tick(aq.UP_AFTER - 1)
    assert auto.poll(played_height=360, tile_height=1080) is None
    clock.tick(2)
    assert auto.poll(played_height=360, tile_height=1080) == "480p"


def test_unknown_variants_can_drop_below_a_duplicate_middle_level():
    """The 720p and 480p caps both play 480p; the 360p cap still helps."""
    auto, clock = make(heights=())
    clock.tick(15)
    auto.poll(played_height=480, tile_height=1080)
    clock.tick(15)
    stall(auto, clock, 9)
    assert auto.poll(played_height=480, tile_height=1080) == "480p"
    auto.loaded(())
    clock.tick(15)
    auto.poll(played_height=480, tile_height=1080)
    clock.tick(15)
    stall(auto, clock, 9)
    assert auto.poll(played_height=480, tile_height=1080) == "360p"


def test_unknown_variants_can_climb_past_a_duplicate_middle_level():
    """The 360p and 480p caps both play 360p; trying 720p still helps."""
    auto, clock = make(heights=())
    auto.reset(tile_height=300)
    auto.loaded(())
    clock.tick(15)
    auto.poll(played_height=360, tile_height=1080)
    clock.tick(aq.UP_AFTER)
    assert auto.poll(played_height=360, tile_height=1080) == "480p"
    auto.loaded(())
    clock.tick(15)
    assert auto.poll(played_height=360, tile_height=1080) is None
    clock.tick(aq.UP_AFTER)
    assert auto.poll(played_height=360, tile_height=1080) == "720p"
    auto.loaded(())
    clock.tick(15)
    assert auto.poll(played_height=720, tile_height=1080) is None
    # The same 480p cap is useful when coming back down from a real 720p picture.
    clock.tick(15)
    stall(auto, clock, 9)
    assert auto.poll(played_height=720, tile_height=1080) == "480p"


def test_unknown_duplicate_levels_are_not_retried_forever():
    """A stream with only 480p is probed at most once per other cap."""
    auto, clock = make(heights=())
    for expected in ("source", None):
        clock.tick(aq.UP_AFTER + 1)
        assert auto.poll(played_height=480, tile_height=1080) == expected
        if expected is not None:
            auto.loaded(())
            clock.tick(aq.HEIGHT_SETTLE + 1)
            auto.poll(played_height=480, tile_height=1080)
    for expected in ("480p", "360p", None):
        clock.tick(30)
        stall(auto, clock, 9)
        assert auto.poll(played_height=480, tile_height=1080) == expected
        if expected is not None:
            auto.loaded(())
            clock.tick(aq.HEIGHT_SETTLE + 1)
            auto.poll(played_height=480, tile_height=1080)
    for _ in range(5):
        clock.tick(aq.UP_AFTER_MAX + 1)
        assert auto.poll(played_height=480, tile_height=1080) is None


def test_new_channel_forgets_previously_duplicate_caps():
    auto, clock = make(heights=())
    clock.tick(aq.UP_AFTER + 1)
    assert auto.poll(played_height=720, tile_height=2160) == "source"
    auto.loaded(())
    clock.tick(aq.HEIGHT_SETTLE + 1)
    auto.poll(played_height=720, tile_height=2160)
    clock.tick(aq.UP_AFTER + 1)
    assert auto.poll(played_height=720, tile_height=2160) is None
    auto.reset()
    auto.loaded(())
    clock.tick(aq.UP_AFTER + 1)
    assert auto.poll(played_height=720, tile_height=2160) == "source"


def test_switching_variant_lookup_modes_discards_old_observations():
    auto, clock = make(heights=())
    clock.tick(aq.UP_AFTER + 1)
    assert auto.poll(played_height=720, tile_height=2160) == "source"
    auto.loaded(())
    clock.tick(aq.HEIGHT_SETTLE + 1)
    auto.poll(played_height=720, tile_height=2160)
    auto.loaded((360, 480, 720, 1080), height=720)
    clock.tick(aq.UP_AFTER + 1)
    assert auto.poll(played_height=720, tile_height=2160) == "source"
    auto.loaded(())
    clock.tick(aq.HEIGHT_SETTLE + 1)
    auto.poll(played_height=1080, tile_height=2160)
    clock.tick(30)
    stall(auto, clock, 9)
    # The preceding unknown lookup's 720p/source equivalence must not survive.
    assert auto.poll(played_height=1080, tile_height=2160) == "720p"


def test_pick_height_matches_the_stream_lookup():
    names = ["160p", "360p", "480p", "720p60", "1080p60", "best", "worst", "audio_only"]
    for limit in (360, 480, 720, 0):
        chosen = streams._pick_streamlink(names, limit)
        assert aq.pick_height(streams._streamlink_heights(names), limit) == streams._streamlink_heights([chosen])[0]
    assert streams._streamlink_heights(names) == (160, 360, 480, 720, 1080)
    assert aq.pick_height((720, 1080), 360) == 720  # nothing that small: the smallest there is
    assert [name for name, _h in aq.LEVELS] == [key for key, _h, _l in streams.QUALITIES if key != streams.AUTO]


def test_auto_is_a_stored_quality_and_never_reaches_the_lookup_by_itself():
    assert streams.normalize_quality("auto") == "auto"
    assert streams.QUALITIES[0][0] == streams.AUTO
