"""Parameter management tools for COMSOL MCP Server."""

from typing import Optional, Union
from mcp.server.fastmcp import FastMCP

from .session import session_manager
from ..utils.studies import find_study


def register_parameter_tools(mcp: FastMCP) -> None:
    """Register parameter management tools with the MCP server."""
    
    @mcp.tool()
    def param_get(
        name: str,
        model_name: Optional[str] = None,
        evaluate: bool = False
    ) -> dict:
        """
        Get the value of a model parameter.
        
        Args:
            name: Parameter name
            model_name: Model name (default: current model)
            evaluate: If True, return evaluated numerical value; if False, return expression string
        
        Returns:
            Parameter value and description, or error message
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            value = model.parameter(name, evaluate=evaluate)
            description = model.description(name)
            
            return {
                "success": True,
                "parameter": name,
                "value": value,
                "description": description,
                "evaluated": evaluate,
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to get parameter: {str(e)}"}
    
    @mcp.tool()
    def param_set(
        name: str,
        value: str,
        model_name: Optional[str] = None,
        description: Optional[str] = None
    ) -> dict:
        """
        Set the value of a model parameter.
        
        Args:
            name: Parameter name
            value: Parameter value (can include units, e.g., "5[V]", "1.5[mm]")
            model_name: Model name (default: current model)
            description: Optional description for the parameter
        
        Returns:
            Confirmation with new value, or error message
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            model.parameter(name, value)
            
            if description:
                model.description(name, description)
            
            return {
                "success": True,
                "parameter": name,
                "value": value,
                "description": description,
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to set parameter: {str(e)}"}
    
    @mcp.tool()
    def param_list(
        model_name: Optional[str] = None,
        evaluate: bool = False
    ) -> dict:
        """
        List all parameters in a model.
        
        Args:
            model_name: Model name (default: current model)
            evaluate: If True, return numerical values; if False, return expressions
        
        Returns:
            Dictionary of all parameters with values and descriptions
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            params = model.parameters(evaluate=evaluate)
            descriptions = model.descriptions()
            
            param_list = []
            for name, value in params.items():
                param_list.append({
                    "name": name,
                    "value": value,
                    "description": descriptions.get(name, ""),
                })
            
            return {
                "success": True,
                "parameters": param_list,
                "count": len(param_list),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to list parameters: {str(e)}"}
    
    @mcp.tool()
    def param_sweep_setup(
        parameter_name: str,
        values: list[Union[str, float]],
        model_name: Optional[str] = None,
        study_name: Optional[str] = None,
        sweep_tag: Optional[str] = None,
        parameter_unit: Optional[str] = None,
    ) -> dict:
        """
        Set values for one parameter in a study-level sweep while retaining its other parameters.
        
        Args:
            parameter_name: Name of the parameter to sweep
            values: List of parameter values to sweep through
            model_name: Model name (default: current model)
            study_name: Study tag or label (default: first study)
            sweep_tag: Parametric step tag. When omitted, use the only sweep or create one.
            parameter_unit: Optional unit for the sweep values, e.g. 'mm'
        
        Returns:
            Sweep configuration confirmation, or error message
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        if not parameter_name.strip() or not values or any(not str(value).strip() for value in values):
            return {"success": False, "error": "Provide a parameter name and at least one sweep value."}
        try:
            study = find_study(model.java, study_name)
            steps = study.feature()
            tags = [str(tag) for tag in steps.tags()]
            sweeps = [tag for tag in tags if str(study.feature(tag).getType()) == "Parametric"]
            if sweep_tag is None and len(sweeps) > 1:
                return {"success": False, "error": f"Specify one of the Parametric step tags: {sweeps}."}
            tag = sweep_tag or (sweeps[0] if sweeps else str(steps.uniquetag("param")))
            if tag in tags and tag not in sweeps:
                return {"success": False, "error": f"Study step {tag!r} is not a Parametric step."}

            created = tag not in tags
            sweep = study.create(tag, "Parametric") if created else study.feature(tag)
            original = None
            try:
                original = {key: list(sweep.getStringArray(key)) for key in ("pname", "plistarr", "punit")}
                names = [str(name) for name in original["pname"]]
                if parameter_name in names:
                    index = names.index(parameter_name)
                else:
                    index = names.index("") if "" in names else len(names)
                    sweep.setIndex("pname", parameter_name, index)
                sweep.setIndex("plistarr", " ".join(str(value) for value in values), index)
                if parameter_unit is not None:
                    sweep.setIndex("punit", parameter_unit, index)
                if created:
                    steps.move(tag, 0)
            except Exception:
                if created:
                    steps.remove(tag)
                elif original is not None:
                    for key, previous in original.items():
                        sweep.set(key, previous)
                raise

            return {
                "success": True,
                "study": str(study.tag()),
                "parameter": parameter_name,
                "values": values,
                "sweep_node": tag,
                "expression": str(sweep.getStringArray("plistarr")[index]),
                "unit": str(sweep.getStringArray("punit")[index]),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to set up parametric sweep: {str(e)}"}
    
    @mcp.tool()
    def param_description(
        name: str,
        text: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Get or set the description of a parameter.
        
        Args:
            name: Parameter name
            text: New description text (if None, returns current description)
            model_name: Model name (default: current model)
        
        Returns:
            Parameter description, or confirmation of update
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            if text is not None:
                model.description(name, text)
                return {
                    "success": True,
                    "parameter": name,
                    "description": text,
                }
            else:
                description = model.description(name)
                return {
                    "success": True,
                    "parameter": name,
                    "description": description,
                }
        except Exception as e:
            return {"success": False, "error": f"Failed to get/set description: {str(e)}"}
