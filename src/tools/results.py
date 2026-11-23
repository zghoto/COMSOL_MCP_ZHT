"""Results evaluation and export tools for COMSOL MCP Server."""

from typing import Literal, Optional, Union, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from mcp.server.fastmcp import FastMCP

from .session import session_manager
from .modify import _apply_properties, _apply_selection
from ..utils.numerical import dataset_tag, read_numerical


def _result_geometry(java_model, data_tag, component_name, geometry_name):
    data = java_model.result().dataset(data_tag)
    data_geometry = str(data.getString("geom")) if data.hasProperty("geom") else None
    candidates = []
    for tag in java_model.component().tags():
        if component_name is not None and component_name != str(tag):
            continue
        component = java_model.component(str(tag))
        for geom in component.geom().tags():
            if geometry_name is not None and geometry_name != str(geom):
                continue
            candidates.append((str(tag), str(geom)))
    if data_geometry and data_geometry not in {"none", "parent"}:
        candidates = [entry for entry in candidates if entry[1] == data_geometry]
    if len(candidates) != 1:
        raise ValueError(f"Choose a component and geometry for dataset {data_tag!r}. Matches: {candidates}.")
    component, geometry = candidates[0]
    return java_model.component(component).geom(geometry), component


def _set_plot_properties(node, properties):
    changes = _apply_properties(node, properties or {})
    if changes["failed"]:
        raise ValueError(f"Rejected plot properties: {changes['failed']}")


def _create_plot_feature(container, spec):
    listing = container.feature()
    tag = str(listing.uniquetag("plot"))
    feature = container.create(tag, spec["type"])
    _set_plot_properties(feature, spec.get("properties"))
    if spec.get("selection") is not None:
        selection = spec["selection"]
        if spec.get("geometry") is not None:
            feature.selection().geom(spec["geometry"], spec["dimension"])
        applied = _apply_selection(feature, selection)
        if "selection_error" in applied:
            raise ValueError(applied["selection_error"])
        if isinstance(selection, list):
            actual = {int(index) for index in feature.selection().entities()}
            if actual != {int(index) for index in selection}:
                raise ValueError(f"Plot feature {tag} selected {sorted(actual)}, requested {selection}.")
    for child in spec.get("subfeatures", []):
        _create_plot_feature(feature, child)
    return tag


def register_results_tools(mcp: FastMCP) -> None:
    """Register results tools with the MCP server."""

    @mcp.tool()
    def results_stat(
        operation: Literal["max", "min", "average", "integral"],
        expression: str,
        entity_type: Literal["domain", "boundary"],
        entities: Union[list[int], str],
        unit: Optional[str] = None,
        dataset: Optional[str] = None,
        model_name: Optional[str] = None,
        component_name: Optional[str] = None,
        geometry_name: Optional[str] = None,
        outer: Optional[int] = None,
        revolve_axisymmetric: Optional[bool] = None,
    ) -> dict:
        """Evaluate derived values on selected domains or boundaries of a 2D or 3D solution.

        Supply entity IDs, a named selection tag, or 'all'. Select a dataset
        explicitly when several solutions exist. Results preserve all selected
        inner solution points. Use abs(expr) to measure a complex amplitude.
        For 2D axisymmetry, revolve_axisymmetric chooses the 2*pi*r weighting
        for averages and integrals. It defaults to COMSOL's setting.
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        try:
            jm = model.java
            data_tags = [str(tag) for tag in jm.result().dataset().tags()]
            if not data_tags:
                raise ValueError("No results datasets. Solve the model or create a dataset first.")
            data_tag = dataset_tag(jm, dataset) if dataset else data_tags[0]
            geom, component = _result_geometry(jm, data_tag, component_name, geometry_name)
            dim = int(geom.getSDim())
            if dim not in (2, 3):
                raise ValueError("Derived domain and boundary statistics require a 2D or 3D geometry.")
            entity_dim = dim if entity_type == "domain" else dim - 1
            dimension_types = {1: "Line", 2: "Surface", 3: "Volume"}
            operations = {"max": "Max", "min": "Min", "average": "Av", "integral": "Int"}
            node_type = operations[operation] + dimension_types[entity_dim]
            listing = jm.result().numerical()
            tag = str(listing.uniquetag("mcpstat"))
            node = listing.create(tag, node_type)
            try:
                node.set("data", data_tag)
                node.set("expr", [expression])
                if unit:
                    node.set("unit", [unit])
                node.selection().geom(str(geom.tag()), entity_dim)
                selected = _apply_selection(node, entities)
                if "selection_error" in selected:
                    raise ValueError(selected["selection_error"])
                applied = [int(index) for index in node.selection().entities()]
                if not applied or (isinstance(entities, list) and set(applied) != set(entities)):
                    raise ValueError(f"COMSOL applied entity IDs {applied}, requested {entities}.")
                weight_property = "intvolume" if entity_dim == 2 else "intsurface"
                weighted = None
                if dim == 2 and operation in {"average", "integral"} and node.hasProperty(weight_property):
                    if revolve_axisymmetric is not None:
                        node.set(weight_property, revolve_axisymmetric)
                    weighted = bool(geom.isAxisymmetric()) and bool(node.getBoolean(weight_property))
                elif revolve_axisymmetric is not None:
                    raise ValueError("Axisymmetric weighting applies to 2D averages and integrals.")
                result = read_numerical(jm, node, outer)
                if result.get("values") == []:
                    raise ValueError("No values were computed for the selected result.")
            finally:
                listing.remove(tag)
            result.update(success=True, operation=operation, entity_type=entity_type,
                          entities=applied, component=component, geometry=str(geom.tag()))
            if weighted is not None:
                result["axisymmetric_weighted"] = weighted
            return result
        except Exception as exc:
            return {"success": False, "error": f"Failed to evaluate derived value: {exc}"}

    @mcp.tool()
    def results_plot_add(
        group_type: Literal["PlotGroup1D", "PlotGroup2D", "PlotGroup3D"],
        features: list[dict],
        dataset: Optional[str] = None,
        label: Optional[str] = None,
        properties: Optional[dict] = None,
        model_name: Optional[str] = None,
    ) -> dict:
        """Build one new plot group. Each feature specifies type and optional
        properties, selection, geometry and dimension, and nested subfeatures.
        Existing groups are kept and a failed new group is removed.
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        if not features:
            return {"success": False, "error": "Supply at least one plot feature."}
        try:
            results = model.java.result()
            data_tag = dataset_tag(model.java, dataset) if dataset else None
            tag = str(results.uniquetag("mcppg"))
            group = results.create(tag, group_type)
            try:
                _set_plot_properties(group, properties)
                if data_tag:
                    group.set("data", data_tag)
                if label:
                    group.label(label)
                tags = [_create_plot_feature(group, spec) for spec in features]
                group.run()
            except Exception:
                results.remove(tag)
                raise
            return {"success": True, "plot_group": tag, "features": tags,
                    "dataset": str(group.getString("data"))}
        except Exception as exc:
            return {"success": False, "error": f"Failed to create plot group: {exc}"}
    
    @mcp.tool()
    def results_evaluate(
        expression: Union[str, Sequence[str]],
        unit: Optional[str] = None,
        dataset: Optional[str] = None,
        inner: Optional[Union[int, str, Sequence[int]]] = None,
        outer: Optional[Union[int, Sequence[int]]] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Evaluate an expression on a solution dataset.
        
        Args:
            expression: Expression(s) to evaluate, e.g., "es.normE" or ["x", "y", "es.normE"]
            unit: Desired unit for result, e.g., "V/m", "pF"
            dataset: Dataset name (default: uses default dataset)
            inner: For time-dependent solutions: index, 'first', 'last', or list of indices
            outer: For parametric sweeps: index or list of indices
            model_name: Model name (default: current model)
        
        Returns:
            Evaluated values as lists, or error message
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            result = model.evaluate(
                expression,
                unit=unit,
                dataset=dataset,
                inner=inner,
                outer=outer,
            )
            
            import numpy as np
            if isinstance(result, np.ndarray):
                if result.ndim == 0:
                    value = float(result)
                else:
                    value = result.tolist()
            elif isinstance(result, (list, tuple)):
                value = [v.tolist() if hasattr(v, 'tolist') else v for v in result]
            else:
                value = result
            
            return {
                "success": True,
                "expression": expression,
                "unit": unit,
                "dataset": dataset,
                "value": value,
                "shape": getattr(result, 'shape', None),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to evaluate: {str(e)}"}
    
    @mcp.tool()
    def results_global_evaluate(
        expression: str,
        unit: Optional[str] = None,
        dataset: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Evaluate a global expression (returns a single scalar value).
        
        Common global expressions include:
        - Integration: "intop1(T)" where intop1 is an integration operator
        - Maximum: "maxop1(T)" 
        - Derived values: "2*es.intWe/U^2" for capacitance
        
        Args:
            expression: Global expression to evaluate
            unit: Desired unit for result
            dataset: Dataset name
            model_name: Model name (default: current model)
        
        Returns:
            Single numerical value
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            result = model.evaluate(expression, unit=unit, dataset=dataset)
            
            import numpy as np
            if isinstance(result, np.ndarray):
                value = float(result.flatten()[0])
            else:
                value = float(result)
            
            return {
                "success": True,
                "expression": expression,
                "unit": unit,
                "value": value,
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to evaluate global expression: {str(e)}"}
    
    @mcp.tool()
    def results_inner_values(
        dataset: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Get inner solution indices and values (time steps in time-dependent study).
        
        Args:
            dataset: Dataset name (default: default dataset)
            model_name: Model name (default: current model)
        
        Returns:
            Arrays of indices and corresponding values (e.g., time values)
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            indices, values = model.inner(dataset)
            
            return {
                "success": True,
                "dataset": dataset,
                "indices": indices.tolist() if hasattr(indices, 'tolist') else list(indices),
                "values": values.tolist() if hasattr(values, 'tolist') else list(values),
                "count": len(values),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to get inner values: {str(e)}"}
    
    @mcp.tool()
    def results_outer_values(
        dataset: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Get outer solution indices and values (parameter values in parametric sweep).
        
        Args:
            dataset: Dataset name (default: default dataset)
            model_name: Model name (default: current model)
        
        Returns:
            Arrays of indices and corresponding parameter values
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            indices, values = model.outer(dataset)
            
            return {
                "success": True,
                "dataset": dataset,
                "indices": indices.tolist() if hasattr(indices, 'tolist') else list(indices),
                "values": values.tolist() if hasattr(values, 'tolist') else list(values),
                "count": len(values),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to get outer values: {str(e)}"}
    
    @mcp.tool()
    def results_export_data(
        node_name: Optional[str] = None,
        file_path: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Export data from an export node.
        
        Args:
            node_name: Export node name (default: run all exports)
            file_path: Output file path (overrides node setting)
            model_name: Model name (default: current model)
        
        Returns:
            Export confirmation with file path
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            model.export(node_name, file_path)
            
            return {
                "success": True,
                "node": node_name,
                "file": file_path,
                "message": f"Export completed: {node_name or 'all exports'}",
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to export data: {str(e)}"}
    
    @mcp.tool()
    def results_export_image(
        node_name: Optional[str] = None,
        file_path: Optional[str] = None,
        model_name: Optional[str] = None,
        plot_group: Optional[str] = None,
        width: int = 1400,
        height: int = 900,
    ) -> dict:
        """
        Export a plot as an image.
        
        Args:
            node_name: Plot export node name
            file_path: Output image path (e.g., "results.png", "field.png")
            model_name: Model name (default: current model)
            plot_group: Plot group tag for direct PNG export using a temporary node
            width: Direct PNG width in pixels
            height: Direct PNG height in pixels
        
        Returns:
            Export confirmation with file path
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            if plot_group:
                if node_name or not file_path or width < 1 or height < 1:
                    raise ValueError("Direct plot export needs a PNG path, positive dimensions, and no export node name.")
                target = Path(file_path).resolve()
                if target.suffix.lower() != ".png":
                    raise ValueError("Use a .png path for direct plot group export.")
                result = model.java.result()
                if plot_group not in [str(tag) for tag in result.tags()]:
                    raise ValueError(f"Plot group {plot_group!r} does not exist.")
                target.parent.mkdir(parents=True, exist_ok=True)
                exports = result.export()
                tag = str(exports.uniquetag("mcpimg"))
                with TemporaryDirectory(prefix=".comsol-image-", dir=target.parent) as directory:
                    staging = Path(directory) / "plot.png"
                    image = exports.create(tag, plot_group, "Image")
                    try:
                        image.set("imagetype", "png")
                        image.set("pngfilename", str(staging))
                        image.set("size", "manualweb")
                        image.set("unit", "px")
                        image.set("width", str(width))
                        image.set("height", str(height))
                        image.set("background", "color")
                        image.run()
                    finally:
                        exports.remove(tag)
                    with staging.open("rb") as stream:
                        header = stream.read(24)
                    if (header[:8] != b"\x89PNG\r\n\x1a\n" or
                        int.from_bytes(header[16:20], "big") != width or
                        int.from_bytes(header[20:24], "big") != height):
                        raise ValueError("COMSOL did not produce a PNG with the requested dimensions.")
                    staging.replace(target)
                return {"success": True, "plot_group": plot_group, "file": str(target),
                        "width": width, "height": height}
            model.export(node_name, file_path)
            
            return {
                "success": True,
                "node": node_name,
                "file": file_path,
                "message": f"Image exported to: {file_path}",
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to export image: {str(e)}"}
    
    @mcp.tool()
    def results_exports_list(model_name: Optional[str] = None) -> dict:
        """
        List all export nodes defined in a model.
        
        Args:
            model_name: Model name (default: current model)
        
        Returns:
            List of export node names
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            exports = model.exports()
            return {
                "success": True,
                "exports": exports,
                "count": len(exports),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to list exports: {str(e)}"}
    
    @mcp.tool()
    def results_plots_list(model_name: Optional[str] = None) -> dict:
        """
        List all plot nodes defined in a model.
        
        Args:
            model_name: Model name (default: current model)
        
        Returns:
            List of plot node names
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            plots = model.plots()
            return {
                "success": True,
                "plots": plots,
                "count": len(plots),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to list plots: {str(e)}"}
