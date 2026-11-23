"""Read COMSOL numerical results without discarding solution or expression axes."""

import math

from jpype import JInt


def dataset_tag(java_model, reference):
    tags = [str(tag) for tag in java_model.result().dataset().tags()]
    if reference in tags:
        return reference
    matches = [tag for tag in tags if str(java_model.result().dataset(tag).label()) == reference]
    if len(matches) != 1:
        raise ValueError(f"Dataset {reference!r} does not identify one dataset. Available tags: {tags}.")
    return matches[0]


def _data_solution(java_model, reference):
    results = java_model.result()
    datasets = {str(tag) for tag in results.dataset().tags()}
    solvers = {str(tag) for tag in java_model.sol().tags()}
    visited = set()
    while reference in datasets and reference not in visited:
        visited.add(reference)
        data = results.dataset(reference)
        if data.hasProperty("solution"):
            tag = str(data.getString("solution"))
            if tag in solvers:
                return java_model.sol(tag)
        reference = str(data.getString("data")) if data.hasProperty("data") else None
    return None


def _matrix(data):
    def number(value):
        value = float(value)
        return value if math.isfinite(value) else None

    return [[number(value) for value in row] if hasattr(row, "__iter__") else [number(row)]
            for row in data]


def _inner_ids(node, info, outer, count):
    indices = [int(index) for index in info.getSolnum(outer or 1, True)]
    mode = str(node.getString("innerinput")) if node.hasProperty("innerinput") else "all"
    if mode == "first":
        indices = indices[:1]
    elif mode == "last":
        indices = indices[-1:]
    elif mode == "manual":
        indices = [int(index) for index in node.getIntArray("solnum")]
    elif mode == "indices":
        indices = [int(index) for index in node.getDoubleArray("solnumindices")]
    elif mode != "all":
        return None
    return indices if len(indices) == count else None


def _solution_axes(node, solver, info, outer, count):
    axes = {"outer_index": outer, "inner_count": count}
    try:
        indices = _inner_ids(node, info, outer, count)
        if indices is None:
            return axes
        axes["inner_indices"] = indices
        if not list(info.getOuterSolnum()):
            values = [float(value) for value in solver.getPVals()]
            names = [str(name) for name in solver.getPNames()]
            if not any(names) and str(solver.getType()) in {"Time", "Transient"}:
                names = ["t"]
            total = len(info.getSolnum(1, True))
            if names and all(names) and len(values) == total * len(names) and indices:
                rows = _matrix(values[(index - 1) * len(names):index * len(names)] for index in indices)
                if len(names) == 1:
                    axes.update(inner_parameter=names[0], inner_values=[row[0] for row in rows])
                else:
                    axes["parameters"] = [dict(zip(names, row)) for row in rows]
        else:
            requests = [[outer, index] for index in indices]
            names = [[str(name) for name in row] for row in info.getPNames(requests)]
            values = _matrix(info.getPvals(requests))
            if len(names) == len(values) == count:
                axes["parameters"] = [dict(zip(row_names, row_values))
                                      for row_names, row_values in zip(names, values)]
    except Exception as exc:
        axes["metadata_error"] = str(exc)
    return axes


def read_numerical(java_model, node, outer=None):
    """Return real/imaginary matrices and match axes to COMSOL solution indices."""
    if outer is not None and outer < 1:
        raise ValueError("Outer solution indices start at 1.")
    data = str(node.getString("data")) if node.hasProperty("data") else None
    solver = _data_solution(java_model, data)
    info = solver.getSolutioninfo() if solver is not None and not solver.isEmpty() else None
    available = [int(index) for index in info.getOuterSolnum()] if info is not None else []
    if outer is not None and (available or info is not None) and outer not in (available or [1]):
        raise ValueError(f"Outer solution {outer} not found. Available: {available or [1]}.")
    effective = [outer] if outer is not None else available
    if outer is None and available and node.hasProperty("outerinput"):
        mode = str(node.getString("outerinput"))
        if mode == "first":
            effective = available[:1]
        elif mode == "last":
            effective = available[-1:]
        elif mode == "manual":
            effective = [int(index) for index in node.getIntArray("outersolnum")]
        elif mode == "indices":
            effective = [int(index) for index in node.getDoubleArray("outersolnumindices")]
        elif mode != "all":
            raise ValueError(f"Choose an explicit outer solution for selection mode {mode!r}.")
        if not effective or any(index not in available for index in effective):
            raise ValueError(f"The selected outer indices {effective} are unavailable. Available: {available}.")
    results = []
    for index in effective or [None]:
        args = (JInt(index),) if index is not None and (available or info is None) else ()
        real = _matrix(node.getReal(*args))
        count = len(real[0]) if real else 0
        axis = (_solution_axes(node, solver, info, index, count) if info is not None
                else {"outer_index": index, "inner_count": count})
        item = {"axis": axis, "real": real}
        if node.isComplex(*args):
            item["imaginary"] = _matrix(node.getImag(*args))
        results.append(item)

    units = [str(unit) for unit in node.getStringArray("unit")] if node.hasProperty("unit") else []
    expressions = ([str(expr) for expr in node.getStringArray("expr")]
                   if node.hasProperty("expr") else [])
    # Retain the original flat values field for one expression on one solution.
    # Multiple expressions or outer points use the explicitly labelled series.
    payload = {"dataset": data, "expressions": expressions, "units": units}
    if len(results) == 1 and len(results[0]["real"]) <= 1:
        item = results[0]
        payload["values"] = item["real"][0] if item["real"] else []
        payload["axis"] = item["axis"]
        if item.get("imaginary"):
            payload["imaginary_values"] = item["imaginary"][0]
    else:
        payload["series"] = results
    return payload
