"""BRD 10.3 calculations, table-driven (ANL-001, ANL-002, ANL-003, ANL-006, ANL-007)."""
import pytest

from app.crm_core import training_scoring as s

Q = [
    {"item_key": "k1", "type": "single", "measure": "knowledge", "options": ["a", "b"], "correct_options": [1], "points": 1, "objective": "LO1"},
    {"item_key": "k2", "type": "multi", "measure": "knowledge", "options": ["a", "b", "c"], "correct_options": [0, 2], "points": 3, "objective": "LO2"},
    {"item_key": "k3", "type": "true_false", "measure": "knowledge", "options": ["True", "False"], "correct_options": [0], "points": 1, "objective": "LO1"},
    {"item_key": "c1", "type": "likert5", "measure": "confidence", "objective": "LO1"},
    {"item_key": "c2", "type": "likert5", "measure": "confidence", "objective": "LO2"},
    {"item_key": "f1", "type": "rating10", "measure": "feedback"},
]


@pytest.mark.parametrize("answers, keys, expected", [
    ({"k1": 1, "k2": [0, 2], "k3": 0}, None, 100.0),
    ({"k1": 0, "k2": [0, 2], "k3": 0}, None, 80.0),          # 4 of 5 points
    ({"k1": 1, "k2": [0], "k3": 1}, None, 20.0),             # multi is all-or-nothing
    ({}, None, 0.0),
    ({"k1": 1, "k2": [2, 0]}, {"k1", "k2"}, 100.0),          # paired keys only
    ({"k1": 1}, {"zz"}, None),                               # nothing paired -> no score
])
def test_anl_001_knowledge_score(answers, keys, expected):
    assert s.knowledge_score(Q, answers, keys) == expected


@pytest.mark.parametrize("answers, expected", [
    ({"c1": 2, "c2": 4}, 3.0), ({"c1": 5}, 5.0), ({}, None), ({"c1": "x"}, None),
])
def test_anl_001_confidence_mean(answers, expected):
    assert s.confidence_score(Q, answers) == expected


@pytest.mark.parametrize("k_pre, k_post, gain, band", [
    (40, 70, 0.5, "Medium"), (0, 20, 0.2, "Low"), (50, 90, 0.8, "High"), (80, 60, -1.0, "Decline"),
    (100, 100, None, None),                                  # ANL-002: excluded at K_pre = 100
    (60, 72, 0.3, "Medium"),                                 # boundary 0.3 is Medium
    (30, 79, 0.7, "High"),                                   # boundary 0.7 is High
])
def test_anl_002_normalized_gain_and_bands(k_pre, k_post, gain, band):
    g = s.normalized_gain(k_pre, k_post)
    assert (g is None and gain is None) or g == pytest.approx(gain)
    assert s.gain_band(g) == band


def test_anl_003_objective_percent_and_achievement():
    answers = {"k1": 1, "k3": 1, "k2": [0, 2]}
    assert s.objective_percent(Q, answers, "LO1") == 50.0
    assert s.objective_percent(Q, answers, "LO2") == 100.0
    assert s.objective_percent(Q, answers, "LO9") is None   # not measured
    assert s.achieved(70, 70) is True and s.achieved(69.9, 70) is False and s.achieved(None, 70) is None


def test_aggregates_are_means_of_participant_values():
    assert s.mean([10, None, 30]) == 20
    assert s.mean([]) is None
    assert s.display(66.666) == 66.7


def test_asm_004_paired_keys():
    pre = [{"item_key": "a", "measure": "knowledge"}, {"item_key": "b", "measure": "confidence"}]
    post = [{"item_key": "a", "measure": "knowledge"}, {"item_key": "b", "measure": "knowledge"}]
    assert s.paired_keys(pre, post, "knowledge") == {"a"}
    assert s.paired_keys(pre, post, "confidence") == set()


@pytest.mark.parametrize("question, value, ok", [
    ({"type": "single", "options": ["a", "b"]}, 1, True),
    ({"type": "single", "options": ["a", "b"]}, 2, False),
    ({"type": "multi", "options": ["a", "b"]}, [], False),
    ({"type": "likert5"}, 6, False),
    ({"type": "nps"}, 0, True),
    ({"type": "rating10"}, 0, False),
    ({"type": "short_text"}, "x" * 501, False),
    ({"type": "long_text", "required": False}, None, True),
    ({"type": "true_false"}, True, False),                   # booleans are not option indexes
])
def test_asm_002_answer_validation(question, value, ok):
    assert (s.answer_problem(question, value) is None) is ok


def test_anl_006_t_distribution_and_wilcoxon_reference_values():
    assert s.t_two_sided_p(2.0, 10) == pytest.approx(0.0734, abs=5e-4)
    assert s.t_two_sided_p(0.0, 5) == pytest.approx(1.0)
    w, p = s.wilcoxon_signed_rank([1, 2, 3, 4, 5])
    assert w == 15 and p == pytest.approx(0.0625)
    w, p = s.wilcoxon_signed_rank([0, 0])
    assert p == 1.0


def test_anl_006_significance_suppressed_below_five_and_test_choice():
    assert s.significance([1, 2, 3, 4], [2, 3, 4, 5]) is None
    small = s.significance([40, 50, 60, 55, 45, 50], [70, 80, 75, 85, 60, 90])
    assert small["test"] == "Wilcoxon signed-rank" and small["significant"] and small["n"] == 6
    pre = [50 + (i % 7) for i in range(30)]
    post = [v + 10 + (i % 3) for i, v in enumerate(pre)]
    big = s.significance(pre, post)
    assert big["test"] == "paired t-test" and big["p_value"] < 0.001 and big["cohens_d"] > 1


def test_anl_007_flags():
    out = s.flags({"LO1": 60.0, "LO2": 90.0, "LO3": None}, 70, confidence_change=1.2, knowledge_change=5)
    assert [f["flag"] for f in out] == ["Knowledge gap", "Confidence-competence mismatch"]
    assert out[0]["objective"] == "LO1"
    assert s.flags({"LO1": 80.0}, 70, confidence_change=1.0, knowledge_change=10) == []
