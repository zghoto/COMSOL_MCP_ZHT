"""Study selection and verification against actual COMSOL solution data."""


def find_study(java_model, reference=None):
    tags = [str(tag) for tag in java_model.study().tags()]
    if not tags:
        raise ValueError("No studies defined in the model.")
    if reference is None:
        return java_model.study(tags[0])
    if reference in tags:
        return java_model.study(reference)
    matches = [tag for tag in tags if str(java_model.study(tag).label()) == reference]
    if len(matches) != 1:
        raise ValueError(f"Study {reference!r} does not identify one study. Use a tag from {tags}.")
    return java_model.study(matches[0])


def solution_diagnostics(solution):
    size = [int(value) for value in solution.getSize()] if not solution.isEmpty() else [0, 0]
    return {
        "tag": str(solution.tag()),
        "study": str(solution.study()),
        "attached": bool(solution.isAttached()),
        "has_data": size[0] > 0 and size[1] > 0,
        "degrees_of_freedom": size[0],
        "solution_count": size[1],
        "warning": str(solution.getWarningMessage() or ""),
        "error": str(solution.getErrorMessage() or ""),
    }


def _has_disabled_equations(java_model, study):
    """Detect basic solve steps that can only store values of unsolved variables."""
    steps = [study.feature(str(tag)) for tag in study.feature().tags()]
    active = [step for step in steps if step.isActive()]
    if not active:
        return True
    solve_types = {"Stationary", "Transient", "Frequency", "Eigenfrequency"}
    if any(str(step.getType()) not in solve_types | {"Parametric"} for step in active):
        return False
    equations = [step for step in active if str(step.getType()) in solve_types]
    if not equations:
        return True
    entities = [f"/physics/{tag}" for tag in java_model.physics().tags()]
    entities.extend(f"/multiphysics/{tag}" for tag in java_model.multiphysics().tags())
    return not any(step.solveFor(uri) for step in equations for uri in entities)


def solve_studies(model, study_name=None, cancelled=None):
    """Run studies and check the result of each requested study independently."""
    jm = model.java
    if study_name is not None:
        studies = [find_study(jm, study_name)]
    else:
        studies = [jm.study(str(tag)) for tag in jm.study().tags()]
    if not studies:
        raise ValueError("No studies defined in the model.")

    runs = []
    for study in studies:
        if cancelled and cancelled():
            break
        tag = str(study.tag())
        if _has_disabled_equations(jm, study):
            runs.append({"study": tag, "success": False, "computed_solutions": [], "solutions": [],
                         "error": f"Study {tag!r} has no enabled equations to solve."})
            break
        before = int(study.getLastComputationDate())
        study.run()
        updated = int(study.getLastComputationDate()) > before
        solutions = [solution_diagnostics(jm.sol(str(ref)))
                     for ref in study.getSolverSequences("SolverSequence")]
        computed = [solution["tag"] for solution in solutions
                    if solution["study"] == tag and solution["attached"]
                    and solution["has_data"] and not solution["error"]]
        run = {"study": tag, "success": updated and bool(computed),
               "computed_solutions": computed if updated else [], "solutions": solutions}
        if not updated:
            run["error"] = f"Study {tag!r} did not report a new computation."
        elif not computed:
            run["error"] = f"Study {tag!r} has no computed data on its attached solver sequence."
        runs.append(run)
        if not run["success"]:
            break
    return runs
