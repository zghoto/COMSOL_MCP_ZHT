"""Study and solving tools for COMSOL MCP Server."""

from typing import Optional
from mcp.server.fastmcp import FastMCP

from .session import session_manager
from ..async_handler.solver import async_solver
from ..utils.studies import find_study
from .modify import _apply_properties


def register_study_tools(mcp: FastMCP) -> None:
    """Register study and solving tools with the MCP server."""
    
    @mcp.tool()
    def study_list(model_name: Optional[str] = None) -> dict:
        """
        List all studies in a model.

        Args:
            model_name: Model name (default: current model)

        Returns:
            List of study names with their types
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }

        try:
            studies = model.studies()

            study_info = []
            for study_name in studies:
                info = {"name": study_name}
                try:
                    study_node = model / "studies" / study_name
                    children = [child.name() for child in study_node.children()]
                    info["steps"] = children
                except Exception:
                    pass
                study_info.append(info)

            return {
                "success": True,
                "studies": study_info,
                "count": len(study_info),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to list studies: {str(e)}"}

    @mcp.tool()
    def study_create(
        study_type: str = "Stationary",
        study_name: Optional[str] = None,
        step_properties: Optional[dict] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Create a new study in the model.

        Common study types:
        - "Stationary": Stationary study (most common for electrostatics, structural)
        - "TimeDependent": Time-dependent study
        - "Eigenfrequency": Eigenfrequency analysis
        - "Frequency": Frequency domain study
        - "Perturbation": Perturbation study

        Args:
            study_type: Type of study to create
            study_name: Optional name/tag for the study
            step_properties: Optional properties set on the study step,
                e.g. {"tlist": "range(0,60[s],3600[s])"} for TimeDependent output
                times, or {"freq": "..."} for Frequency studies
            model_name: Model name (default: current model)

        Returns:
            Created study info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }

        try:
            jm = model.java
            existing_studies = jm.study().size()
            study_tag = study_name or f"std{existing_studies + 1}"

            # COMSOL Java API: study.create(tag, <full step type name>).
            # "Stationary"/"Transient" etc. are the feature type names; the
            # short forms ("stat", "time", ...) are conventional tags.
            STEP_TYPES = {
                "Stationary": ("stat", "Stationary"),
                "stat": ("stat", "Stationary"),
                "TimeDependent": ("time", "Transient"),
                "Transient": ("time", "Transient"),
                "time": ("time", "Transient"),
                "Eigenfrequency": ("eig", "Eigenfrequency"),
                "eig": ("eig", "Eigenfrequency"),
                "Frequency": ("freq", "Frequency"),
                "freq": ("freq", "Frequency"),
            }
            if study_type not in STEP_TYPES:
                return {
                    "success": False,
                    "error": (
                        f"Unknown study type: {study_type}. Use one of: "
                        + ", ".join(sorted(set(STEP_TYPES)))
                    ),
                }
            step_tag, step_type = STEP_TYPES[study_type]

            study = jm.study().create(study_tag)
            # keep the display label equal to the tag so name-based lookups
            # (MPh resolves model/studies/<name> by label) succeed
            study.label(study_tag)
            step = study.create(step_tag, step_type)

            property_failures = {}
            if step_properties:
                for prop_name, prop_value in step_properties.items():
                    try:
                        step.set(prop_name, prop_value)
                    except Exception as e:
                        property_failures[prop_name] = str(e)[:120]

            return {
                "success": not property_failures,
                "study": study_tag,
                "type": study_type,
                "step_type": step_type,
                "step_properties": step_properties or {},
                "property_errors": property_failures or None,
                "model": model.name(),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to create study: {str(e)}"}
    
    @mcp.tool()
    def study_configure_step(
        study_name: str,
        step_tag: str,
        solve_for: Optional[dict[str, bool]] = None,
        properties: Optional[dict] = None,
        model_name: Optional[str] = None,
    ) -> dict:
        """Configure an existing study step. solve_for maps entity paths such
        as /physics/acpr to booleans. Properties belong to the selected step.
        """
        from mph.node import get as mph_get

        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        if async_solver.is_running:
            return {"success": False, "error": "The current solving operation is still running."}
        try:
            study = find_study(model.java, study_name)
            step = study.feature(step_tag)
            switches = solve_for or {}
            fields = properties or {}
            old_switches = {uri: bool(step.solveFor(uri)) for uri in switches}
            old_fields = {name: str(step.label()) if name == "label" else mph_get(step, name) for name in fields}
            touched = []
            applied = []
            try:
                for uri, active in switches.items():
                    touched.append(uri)
                    if not step.setSolveFor(uri, active) or bool(step.solveFor(uri)) != active:
                        raise ValueError(f"COMSOL did not apply solve_for={active} at {uri}.")
                changes = _apply_properties(step, fields)
                applied = changes["applied"]
                if changes["failed"]:
                    raise ValueError(f"Rejected study properties: {changes['failed']}")
                values = {name: str(step.label()) if name == "label" else mph_get(step, name) for name in fields}
                values = {name: value.tolist() if hasattr(value, "tolist") else value for name, value in values.items()}
                return {"success": True, "study": str(study.tag()), "step": step_tag,
                        "solve_for": {uri: bool(step.solveFor(uri)) for uri in switches}, "properties": values}
            except Exception as exc:
                report = {"success": False, "error": str(exc)}
                rollback = _apply_properties(step, {name: old_fields[name] for name in applied})["failed"]
                for uri in reversed(touched):
                    try:
                        if not step.setSolveFor(uri, old_switches[uri]):
                            raise ValueError("COMSOL rejected the original setting.")
                    except Exception as cleanup:
                        rollback.append({"path": uri, "error": str(cleanup)})
                if rollback:
                    report["rollback_errors"] = rollback
                return report
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @mcp.tool()
    def study_solve(
        study_name: Optional[str] = None,
        model_name: Optional[str] = None,
        wait: bool = True,
        timeout: Optional[float] = None,
        log_progress: bool = False,
    ) -> dict:
        """
        Solve a study (synchronous by default).
        
        Args:
            study_name: Study to solve (None for all studies)
            model_name: Model name (default: current model)
            wait: If True, wait for completion; if False, return immediately
            timeout: Maximum wait time in seconds (only used if wait=True)
            log_progress: Capture the actual COMSOL solver log for this run
        
        Returns:
            Solution status, or error message
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        if async_solver.is_running:
            return {
                "success": False,
                "error": "Another solving operation is in progress. Use study_get_progress to check status."
            }
        
        try:
            model_util = session_manager.client.java if log_progress else None
            if not async_solver.start_solve(model, study_name, model_util=model_util):
                return {"success": False, "error": "Another solving operation is in progress."}
            if not wait:
                return {"success": True, "study": study_name, "model": model.name(),
                        "message": "Solving started in background. Use study_get_progress to monitor.", "async": True}
            finished = async_solver.wait(timeout)
            progress = async_solver.get_progress()
            if not finished:
                return {"success": False, "study": study_name, "running": True,
                        "error": "Timed out waiting. The COMSOL solve is still running.", "progress": progress}
            result = {"success": progress["status"] == "completed", "study": study_name,
                      "message": progress["message"]}
            for key in ("runs", "log_file", "log_tail", "error"):
                if key in progress and progress[key] is not None:
                    result[key] = progress[key]
            return result
        except Exception as e:
            return {"success": False, "error": f"Failed to solve: {str(e)}"}
    
    @mcp.tool()
    def study_solve_async(
        study_name: Optional[str] = None,
        model_name: Optional[str] = None,
        log_progress: bool = False,
    ) -> dict:
        """
        Start solving a study in the background (asynchronous).
        
        Use study_get_progress to monitor progress and study_cancel to stop.
        
        Args:
            study_name: Study to solve (None for all studies)
            model_name: Model name (default: current model)
            log_progress: Capture the actual COMSOL solver log for this run
        
        Returns:
            Confirmation that solving started, or error message
        """
        return study_solve(study_name=study_name, model_name=model_name, wait=False, log_progress=log_progress)
    
    @mcp.tool()
    def study_get_progress() -> dict:
        """
        Get the progress of the current solving operation.
        
        Returns:
            Run status and elapsed time, plus the live log tail if requested
        """
        progress = async_solver.get_progress()
        return {
            "success": True,
            "progress": progress,
        }
    
    @mcp.tool()
    def study_cancel() -> dict:
        """
        Cancel the current solving operation.
        
        Note: The solver may take a moment to respond to cancellation.
        
        Returns:
            Cancellation status
        """
        if async_solver.cancel():
            return {
                "success": True,
                "message": "Cancellation requested. COMSOL may finish its current solve before stopping.",
            }
        return {
            "success": False,
            "message": "No solving operation in progress.",
        }
    
    @mcp.tool()
    def study_wait(timeout: Optional[float] = None) -> dict:
        """
        Wait for the current solving operation to complete.
        
        Args:
            timeout: Maximum time to wait in seconds (None for indefinite)
        
        Returns:
            Final progress status
        """
        completed = async_solver.wait(timeout=timeout)
        progress = async_solver.get_progress()
        
        return {
            "success": True,
            "completed": completed,
            "progress": progress,
        }
    
    @mcp.tool()
    def solutions_list(model_name: Optional[str] = None) -> dict:
        """
        List all solutions in a model.
        
        Args:
            model_name: Model name (default: current model)
        
        Returns:
            List of solution configurations
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            solutions = model.solutions()
            return {
                "success": True,
                "solutions": solutions,
                "count": len(solutions),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to list solutions: {str(e)}"}
    
    @mcp.tool()
    def datasets_list(model_name: Optional[str] = None) -> dict:
        """
        List all datasets in a model.
        
        Datasets represent solution data that can be evaluated or visualized.
        
        Args:
            model_name: Model name (default: current model)
        
        Returns:
            List of dataset names
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            datasets = model.datasets()
            results = model.java.result()
            details = []
            for tag in results.dataset().tags():
                node = results.dataset(str(tag))
                detail = {"tag": str(tag), "name": str(node.label()), "type": str(node.getType())}
                if node.hasProperty("solution"):
                    detail["solution"] = str(node.getString("solution"))
                if node.hasProperty("data"):
                    detail["source_dataset"] = str(node.getString("data"))
                details.append(detail)
            return {
                "success": True,
                "datasets": datasets,
                "details": details,
                "count": len(datasets),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to list datasets: {str(e)}"}
