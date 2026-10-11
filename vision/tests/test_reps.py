import numpy as np
import pytest

from vision.physics import PhysicsResult
from vision.reps import ExerciseCounter
from vision.rules import RuleEngine, load_exercise
from vision.types import FrameResult


def frame(t, knee=170, hip=120, **angles):
    values = {"left_knee": knee, "right_knee": knee,
              "left_hip": hip, "right_hip": hip, **angles}
    return FrameResult(t, np.full((33, 3), np.nan), np.zeros(33),
                       angles=values, tracking="3d", body="ready")


def single_frame(t, knee, hip_y):
    f = frame(t, knee=knee)
    f.tracking = "single_cam_B"
    f.joints[[23, 24], 1] = hip_y
    f.image_metrics = {"knee_mean": 175 - (132 - knee) * .85,
                       "hip_y": 400 + (.8 - hip_y) * 200,
                       "torso_px": 125}
    return f


def test_single_camera_squat_uses_relative_knee_and_hip_depth():
    counter = ExerciseCounter("squat")
    deep = [(0, 132, .80), (.2, 110, .70), (.5, 65, .42), (.8, 40, .30),
            (.95, 43, .31), (1.1, 70, .45), (1.4, 100, .70), (1.7, 126, .78)]
    results = [counter.update(single_frame(t, knee, hip_y)) for t, knee, hip_y in deep]
    assert results[-1].result is not None and results[-1].result.accepted
    assert results[-1].rep == 1

    shallow = [(2, 132, .80), (2.2, 108, .70), (2.5, 75, .50), (2.8, 129, .80)]
    results = [counter.update(single_frame(t, knee, hip_y)) for t, knee, hip_y in shallow]
    assert results[-1].result is not None and not results[-1].result.accepted
    assert results[-1].result.quality <= 49 and results[-1].rep == 1


def test_single_camera_squat_must_return_hips_to_standing_height():
    counter = ExerciseCounter("squat")
    sequence = [(0, 135, .8), (.2, 110, .7), (.5, 55, .4), (.8, 45, .3),
                (.95, 45, .3), (1.1, 75, .45), (1.4, 130, .6), (1.7, 130, .78)]
    updates = [counter.update(single_frame(t, knee, hip_y)) for t, knee, hip_y in sequence]
    assert updates[-2].result is None
    assert updates[-1].result is not None and updates[-1].result.accepted


def test_squat_recovers_after_long_frame_gap():
    counter = ExerciseCounter("squat")
    counter.update(single_frame(0, 132, .8))
    counter.update(single_frame(.2, 105, .7))
    assert counter.update(single_frame(1.0, 80, .5)).phase == "idle"
    assert counter.update(single_frame(1.2, 132, .8)).phase == "ready"


def test_single_camera_rejects_3d_depth_created_by_a_weight_shift():
    counter = ExerciseCounter("squat")
    sequence = [(0, 135, .80), (.2, 110, .69), (.5, 55, .39),
                (.7, 48, .34), (.9, 70, .45), (1.2, 110, .70), (1.5, 132, .79)]
    for t, knee, hip_y in sequence:
        f = single_frame(t, knee, hip_y)
        f.image_metrics["knee_mean"] = 170 if t == 0 else 135
        f.image_metrics["hip_y"] = 400 if t == 0 else 440
        update = counter.update(f)
    assert update.result is not None and not update.result.accepted
    assert counter.rep == 0


def test_squat_hysteresis_count_quality_and_set_result():
    counter = ExerciseCounter("squat")
    sequence = [(0, 170), (.2, 148), (.4, 142), (.7, 125), (1, 105),
                (1.2, 112), (1.4, 120), (1.7, 145), (2, 157)]
    results = [counter.update(frame(t, knee=k)) for t, k in sequence]
    assert [u.phase for u in results] == ["ready", "ready", "descending", "descending",
                                          "bottom", "bottom", "ascending", "ascending", "ready"]
    assert results[-1].result.accepted
    assert results[-1].result.quality == 100
    assert results[-1].rep == 1
    summary = counter.finish_set()
    assert summary.accepted_reps == summary.attempts == 1
    assert summary.mean_quality == 100
    assert counter.rep == 0


def test_squat_incomplete_motion_and_fast_attempt_are_not_counted():
    counter = ExerciseCounter("squat")
    values = [(0, 170), (.1, 140), (.2, 120), (.3, 156)]
    for t, knee in values:
        update = counter.update(frame(t, knee=knee))
    assert update.result is not None and not update.result.accepted
    assert update.rep == 0  # target depth never entered
    for t, knee in [(.4, 170), (.5, 140), (.6, 105), (.7, 120), (.8, 156)]:
        update = counter.update(frame(t, knee=knee))
    assert update.result is not None and not update.result.accepted
    assert update.result.quality <= 80 and update.rep == 0


def test_bridge_hysteresis_and_asymmetry_penalty():
    counter = ExerciseCounter("glute_bridge")
    for t, hip in [(0, 120), (.3, 158), (.7, 175), (1, 170), (1.3, 159), (1.8, 130)]:
        update = counter.update(frame(t, hip=hip, right_hip=hip - 20), PhysicsResult(stance="floor"))
    assert update.result is not None and update.result.accepted
    assert update.result.rom_deg >= 25
    assert update.result.max_asymmetry_deg == 20
    assert update.result.quality < 100


def test_bridge_requires_floor_posture():
    counter = ExerciseCounter("glute_bridge")
    for t, hip in [(0, 120), (.3, 158), (.7, 175), (1, 170), (1.3, 149), (1.8, 130)]:
        update = counter.update(frame(t, hip=hip))
    assert update.phase == "idle" and counter.results == []


def test_bridge_uses_joint_floor_posture_without_physics_result():
    counter = ExerciseCounter("glute_bridge")
    for t, hip in [(0, 120), (.3, 158), (.7, 175), (1, 170), (1.3, 149), (1.8, 130)]:
        f = frame(t, hip=hip)
        f.joints[[11, 12], 1] = .35
        f.joints[[23, 24], 1] = .5
        update = counter.update(f)
    assert update.result is not None and update.result.accepted


def test_single_leg_hold_uses_contact_and_exit_dwell():
    counter = ExerciseCounter("single_leg_stance", {"target_hold_s": 2, "min_hold_s": 1})
    left = PhysicsResult(load_l_pct=100, load_conf=.5, stance="left")
    both = PhysicsResult(load_l_pct=50, load_conf=.5, stance="double")
    for t in (0, .15, .3, .6, 1, 1.5, 2, 2.4):
        update = counter.update(frame(t), left)
    assert update.phase == "holding" and update.rep == 0
    assert counter.update(frame(2.5), both).result is None
    result = counter.update(frame(2.75), both).result
    assert result.accepted and result.stance == "left"
    assert result.duration_s == pytest.approx(2.5)
    assert result.quality == 100


def test_rule_duration_cooldown_and_patient_override():
    config = load_exercise("squat", {"rules": {"knee_valgus": {"threshold": .9,
                                                                  "duration_s": .2,
                                                                  "cooldown_s": 1}}})
    rules = RuleEngine(config["rules"])
    def f(t, ratio):
        return frame(t, knee_to_ankle_gap_ratio=ratio)
    assert rules.update(f(0, .8), "bottom") == []
    assert rules.update(f(.1, .8), "bottom") == []
    assert [e.rule_id for e in rules.update(f(.25, .8), "bottom")] == ["knee_valgus"]
    assert rules.active_ids == {"knee_valgus"}
    assert rules.update(f(.4, .8), "bottom") == []
    assert rules.update(f(.5, 1), "bottom") == []
    assert rules.active_ids == set()
    rules.update(f(.6, .8), "bottom")
    assert rules.update(f(.85, .8), "bottom") == []
    assert [e.rule_id for e in rules.update(f(1.3, .8), "bottom")] == ["knee_valgus"]
    with pytest.raises(ValueError):
        load_exercise("squat", {"rules": {"missing": {"threshold": 1}}})
    with pytest.raises(ValueError):
        load_exercise("squat", {"rules": {"knee_valgus": {"duration_s": -1}}})
    with pytest.raises(ValueError):
        load_exercise("squat", {"enter_below": 160})
    with pytest.raises(ValueError):
        load_exercise("squat", {"single_target_drop_deg": 10})
    with pytest.raises(ValueError):
        load_exercise("single_leg_stance", {"target_hold_s": 0})


def test_rule_flag_quality_penalty_and_untrusted_interruption():
    counter = ExerciseCounter("squat")
    updates = []
    for t, knee, ratio in [(0, 170, 1), (.2, 140, .6), (.4, 125, .6),
                           (.7, 105, .6), (1, 115, .6), (1.3, 145, 1), (1.6, 158, 1)]:
        f = frame(t, knee=knee, knee_to_ankle_gap_ratio=ratio)
        updates.append(counter.update(f))
        if t == .7:
            assert "form:knee_valgus" in f.flags
    assert any(e.rule_id == "knee_valgus" for u in updates for e in u.rule_events)
    assert updates[-1].result.violations == ("knee_valgus",)
    assert updates[-1].result.quality < 100
    counter.update(frame(2, knee=170))
    counter.update(frame(2.2, knee=140))
    invalid = frame(2.3, knee=100)
    invalid.flags.append("pose_held")
    assert counter.update(invalid).phase == "idle"
    assert counter.update(frame(2.4, knee=160)).rep == 1
    assert counter.finish_set().attempts == 1


@pytest.mark.parametrize("change", [{"body": "measuring"}, {"tracking": "lost"},
                                        {"flags": ["pose_held"]}, {"flags": ["camera_moved"]}])
def test_invalid_frame_discards_partial_rep_without_changing_count(change):
    counter = ExerciseCounter("squat")
    counter.update(frame(0))
    counter.update(frame(.2, knee=115))
    bad = frame(.3, knee=90)
    for key, value in change.items():
        setattr(bad, key, value)
    update = counter.update(bad)
    assert update.phase == "idle" and update.rep == 0 and update.result is None
    assert counter.update(frame(.4, knee=160)).phase == "ready"


def test_missing_metric_discards_partial_rep():
    counter = ExerciseCounter("squat")
    counter.update(frame(0))
    counter.update(frame(.2, knee=115))
    missing = frame(.3)
    missing.angles.pop("right_knee")
    assert counter.update(missing).phase == "idle"
    assert counter.update(frame(.4, knee=135)).rep == 0
