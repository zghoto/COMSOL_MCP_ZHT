"""Regressions for the COMSOL entity, result, and study APIs."""

from unittest.mock import Mock

from src.tools.audit import geometry_adjacency
from src.utils.numerical import read_numerical
from src.utils.studies import solve_studies


def test_adjacency_uses_comsol_entity_ids():
    geom = Mock()
    geom.getAdj.return_value = [[], [1], [1, 2], [2]]
    assert geometry_adjacency(geom, 2, 3, 3) == [[1], [1, 2], [2]]
    geom.getAdj.assert_called_once_with(2, 3)


def test_complex_numerical_results_keep_every_expression_and_inner_point():
    java = Mock()
    java.result().dataset().tags.return_value = []
    java.sol().tags.return_value = []
    node = Mock()
    node.hasProperty.side_effect = lambda key: key in ("data", "expr", "unit")
    node.getString.return_value = "dset1"
    node.getStringArray.side_effect = {"expr": ["u", "v"], "unit": ["m", "V"]}.__getitem__
    node.getReal.return_value = [[1.0, 2.0], [3.0, 4.0]]
    node.isComplex.return_value = True
    node.getImag.return_value = [[5.0, 6.0], [7.0, 8.0]]

    result = read_numerical(java, node)
    assert result["dataset"] == "dset1"
    assert result["units"] == ["m", "V"]
    assert result["series"] == [
        {"axis": {"outer_index": None, "inner_count": 2},
         "real": [[1.0, 2.0], [3.0, 4.0]], "imaginary": [[5.0, 6.0], [7.0, 8.0]]}
    ]


def test_inner_sweep_keeps_parameter_values_grouped_by_solution():
    java = Mock()
    java.result().dataset().tags.return_value = ["dset1"]
    dataset = Mock()
    dataset.hasProperty.side_effect = lambda key: key == "solution"
    dataset.getString.return_value = "sol1"
    java.result().dataset.side_effect = lambda tag=None: dataset if tag else Mock(tags=lambda: ["dset1"])
    info = Mock()
    info.getOuterSolnum.return_value = []
    info.getSolnum.return_value = [1, 2]
    solver = Mock()
    solver.isEmpty.return_value = False
    solver.getSolutioninfo.return_value = info
    solver.getPNames.return_value = ["amp", "bias"]
    solver.getPVals.return_value = [1.0, 10.0, 2.0, 20.0]
    java.sol.side_effect = lambda tag=None: solver if tag else Mock(tags=lambda: ["sol1"])
    node = Mock()
    node.hasProperty.side_effect = lambda key: key in ("data", "innerinput")
    node.getString.side_effect = {"data": "dset1", "innerinput": "all"}.__getitem__
    node.getReal.return_value = [[11.0, 22.0]]
    node.isComplex.return_value = False

    result = read_numerical(java, node)
    assert result["values"] == [11.0, 22.0]
    assert result["axis"]["parameters"] == [{"amp": 1.0, "bias": 10.0}, {"amp": 2.0, "bias": 20.0}]


def _study_with_old_solution(enabled):
    model = Mock()
    java = model.java
    target = Mock()
    target.tag.return_value = "target"
    target.getLastComputationDate.side_effect = [100, 200]
    target.getSolverSequences.return_value = ["sol_current", "sol_old"]
    step = Mock()
    step.getType.return_value = "Stationary"
    step.isActive.return_value = True
    step.solveFor.return_value = enabled
    features = Mock()
    features.tags.return_value = ["stat"]
    target.feature.side_effect = lambda ref=None: features if ref is None else step
    studies = Mock()
    studies.tags.return_value = ["target"]
    java.study.side_effect = lambda ref=None: studies if ref is None else target
    java.physics().tags.return_value = ["c"]
    java.multiphysics().tags.return_value = []

    old = Mock()
    old.tag.return_value = "sol_old"
    old.study.return_value = "older_study"
    old.isAttached.return_value = True
    old.isEmpty.return_value = False
    old.getSize.return_value = [80, 1]
    old.getErrorMessage.return_value = ""
    old.getWarningMessage.return_value = ""
    current = Mock()
    current.tag.return_value = "sol_current"
    current.study.return_value = "target"
    current.isAttached.return_value = True
    current.isEmpty.return_value = False
    current.getSize.return_value = [0, 1]
    current.getErrorMessage.return_value = ""
    current.getWarningMessage.return_value = ""
    java.sol.side_effect = {"sol_old": old, "sol_current": current}.__getitem__
    return model, target


def test_previous_study_and_current_zero_dof_placeholder_do_not_count():
    model, target = _study_with_old_solution(enabled=True)
    run = solve_studies(model, "target")[0]
    target.run.assert_called_once()
    assert run["success"] is False
    assert run["computed_solutions"] == []
    assert run["solutions"][0]["degrees_of_freedom"] == 0


def test_study_with_all_equations_disabled_is_rejected_before_run():
    model, target = _study_with_old_solution(enabled=False)
    run = solve_studies(model, "target")[0]
    target.run.assert_not_called()
    assert run["success"] is False
    assert "no enabled equations" in run["error"]
