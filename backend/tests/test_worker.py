from app.services.worker import IndexWorker


def test_short_scene_has_one_center_frame():
    assert IndexWorker._frame_timestamps(1_000, 2_000) == [1_500]


def test_long_scene_is_sampled_every_two_seconds():
    assert IndexWorker._frame_timestamps(10_000, 17_000) == [11_000, 13_000, 15_000]
