"""Mesh tools for COMSOL MCP Server."""

from typing import Optional, Union

from mcp.server.fastmcp import FastMCP

from .session import session_manager
from .modify import _apply_properties


def _get_component_java(java_model, component_name=None):
    """Return an explicit component or the model's first component."""
    if component_name:
        return java_model.component(component_name)
    components = java_model.component()
    if components.size() == 0:
        raise ValueError("No components defined in the model.")
    return java_model.component(str(components.tags()[0]))


def _get_geometry_tag(component, geometry_name=None):
    """Return an explicit geometry tag or the component's first geometry."""
    if geometry_name:
        return geometry_name
    geometries = component.geom()
    if geometries.size() == 0:
        raise ValueError("No geometries defined in the component.")
    return str(geometries.tags()[0])


def _mesh_tags(component):
    """Return stable Java tags for all mesh sequences in a component."""
    return [str(tag) for tag in component.mesh().tags()]


def _safe_label(java_node):
    """Return a usable label without leaking Java replacement characters."""
    try:
        label = java_node.label()
        if label and "\ufffd" not in label:
            return str(label)
    except Exception:
        pass
    return None


def _mesh_selection(selection, entities, geometry=None, dimension=None):
    if dimension is not None:
        selection.geom(geometry, dimension)
    if entities == "remaining":
        selection.remaining()
    elif entities == "all":
        selection.all()
    elif isinstance(entities, str):
        selection.named(entities)
    else:
        if not entities or any(index < 1 for index in entities):
            raise ValueError("Provide one or more positive entity IDs.")
        selection.set(entities)
        actual = {int(index) for index in selection.entities()}
        if actual != set(entities):
            raise ValueError(f"Mesh selection applied {sorted(actual)}, requested {entities}.")


def register_mesh_tools(mcp: FastMCP) -> None:
    """Register mesh tools with the MCP server."""

    @mcp.tool()
    def mesh_list(
        component_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        List mesh sequences by stable COMSOL Java tag.

        Args:
            component_name: Component name (default: all components)
            model_name: Model name (default: current model)

        Returns:
            Mesh sequence tags, components, and usable labels
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }

        try:
            java_model = model.java
            components = java_model.component()
            mesh_items = []

            for component_tag in components.tags():
                comp = java_model.component(str(component_tag))
                if component_name and comp.tag() != component_name:
                    continue
                for mesh_tag in comp.mesh().tags():
                    java_mesh = comp.mesh(str(mesh_tag))
                    item = {
                        "tag": str(java_mesh.tag()),
                        "component": str(comp.tag()),
                    }
                    label = _safe_label(java_mesh)
                    if label:
                        item["label"] = label
                    mesh_items.append(item)

            if component_name and not any(
                item["component"] == component_name for item in mesh_items
            ):
                _get_component_java(java_model, component_name)

            return {
                "success": True,
                "meshes": mesh_items,
                "count": len(mesh_items),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to list meshes: {str(e)}"}

    @mcp.tool()
    def mesh_create_sequence(
        mesh_name: str = "mesh1",
        geometry_name: Optional[str] = None,
        component_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Create a mesh sequence bound to a geometry.

        Args:
            mesh_name: Mesh sequence tag (default: "mesh1")
            geometry_name: Geometry sequence tag (default: first geometry)
            component_name: Component name (default: first component)
            model_name: Model name (default: current model)

        Returns:
            Created mesh sequence info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }

        try:
            comp = _get_component_java(model.java, component_name)
            existing_tags = _mesh_tags(comp)
            if mesh_name in existing_tags:
                return {
                    "success": False,
                    "error": f"Mesh sequence already exists: {mesh_name}",
                    "available_meshes": existing_tags,
                }

            geom_tag = _get_geometry_tag(comp, geometry_name)
            java_mesh = comp.mesh().create(mesh_name, geom_tag)

            return {
                "success": True,
                "mesh": {
                    "tag": str(java_mesh.tag()),
                    "component": str(comp.tag()),
                    "geometry": geom_tag,
                },
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to create mesh sequence: {str(e)}"
            }

    @mcp.tool()
    def mesh_create(
        mesh_name: Optional[str] = None,
        geometry_name: Optional[str] = None,
        component_name: Optional[str] = None,
        auto_create: bool = True,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Run one or all mesh sequences, creating a default sequence when needed.

        Args:
            mesh_name: Mesh sequence tag (default: all, or "mesh1" when created)
            geometry_name: Geometry tag used when creating a mesh sequence
            component_name: Component name (default: first component)
            auto_create: Create a missing mesh sequence (default: True)
            model_name: Model name (default: current model)

        Returns:
            Mesh generation status
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }

        try:
            java_model = model.java
            comp = _get_component_java(java_model, component_name)
            mesh_tags = _mesh_tags(comp)
            created_sequence = False

            if mesh_name:
                target_tags = [mesh_name]
                if mesh_name not in mesh_tags:
                    if not auto_create:
                        return {
                            "success": False,
                            "error": f"Mesh sequence not found: {mesh_name}",
                            "available_meshes": mesh_tags,
                        }
                    geom_tag = _get_geometry_tag(comp, geometry_name)
                    comp.mesh().create(mesh_name, geom_tag)
                    created_sequence = True
            elif mesh_tags:
                target_tags = mesh_tags
            elif auto_create:
                geom_tag = _get_geometry_tag(comp, geometry_name)
                comp.mesh().create("mesh1", geom_tag)
                target_tags = ["mesh1"]
                created_sequence = True
            else:
                return {
                    "success": False,
                    "error": "No mesh sequences defined in the model.",
                }

            for target_tag in target_tags:
                comp.mesh(target_tag).run()

            return {
                "success": True,
                "meshes": target_tags,
                "component": str(comp.tag()),
                "auto_created": created_sequence,
                "message": f"Mesh created: {', '.join(target_tags)}",
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to create mesh: {str(e)}"}

    @mcp.tool()
    def mesh_add_feature(
        feature_type: str,
        mesh_name: str = "mesh1",
        component_name: Optional[str] = None,
        geometry_name: Optional[str] = None,
        model_name: Optional[str] = None,
        parent: Optional[str] = None,
        insert_before: Optional[str] = None,
        entity_dim: Optional[int] = None,
        entities: Optional[Union[list[int], str]] = None,
        selections: Optional[dict[str, Union[list[int], str]]] = None,
        properties: Optional[dict] = None,
    ) -> dict:
        """Add a mesh operation or attribute, including Size, Sweep, and BndLayer.

        Use parent for an attribute of an existing step, e.g. BndLayerProp.
        Supply entity IDs, a named selection tag, 'all', or 'remaining'. Use
        insert_before to place the new step before an existing sibling. Run
        mesh_create afterwards to generate the mesh.
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        created_mesh = False
        created_feature = False
        was_automatic = False
        comp = None
        try:
            comp = _get_component_java(model.java, component_name)
            if mesh_name in _mesh_tags(comp):
                mesh = comp.mesh(mesh_name)
                mesh_geometry = str(mesh.geom())
                if geometry_name is not None and geometry_name != mesh_geometry:
                    raise ValueError(f"Mesh {mesh_name!r} is bound to geometry {mesh_geometry!r}.")
                was_automatic = bool(mesh.isAutomatic())
            else:
                if parent or insert_before:
                    raise ValueError(f"Mesh {mesh_name!r} is required for parent or insert_before.")
                mesh_geometry = _get_geometry_tag(comp, geometry_name)
                mesh = comp.mesh().create(mesh_name, mesh_geometry)
                created_mesh = True

            container = mesh.feature(parent) if parent else mesh
            features = container.feature()
            if insert_before and insert_before not in [str(tag) for tag in features.tags()]:
                raise ValueError(f"Mesh step {insert_before!r} is not a sibling in the selected mesh path.")
            feature_tag = str(features.uniquetag("step"))
            feature = container.create(feature_tag, feature_type)
            created_feature = True
            report = _apply_properties(feature, properties or {})
            if report["failed"]:
                raise ValueError(str(report["failed"]))
            if entities is not None:
                _mesh_selection(feature.selection(), entities, mesh_geometry, entity_dim)
            for key, values in (selections or {}).items():
                _mesh_selection(feature.selection(key), values)
            if insert_before:
                siblings = [str(tag) for tag in features.tags() if str(tag) != feature_tag]
                features.move(feature_tag, siblings.index(insert_before))
            return {"success": True, "model": model.name(), "component": str(comp.tag()),
                    "geometry": mesh_geometry, "mesh": mesh_name, "tag": feature_tag,
                    "type": feature_type, "parent": parent}
        except Exception as exc:
            error = {"success": False, "error": f"Failed to add mesh feature: {exc}"}
            try:
                if created_mesh:
                    comp.mesh().remove(mesh_name)
                else:
                    if created_feature:
                        features.remove(feature_tag)
                    if was_automatic and not mesh.isAutomatic():
                        mesh.automatic(True)
            except Exception as cleanup:
                error["cleanup_error"] = str(cleanup)
            return error

    @mcp.tool()
    def mesh_info(
        mesh_name: Optional[str] = None,
        component_name: Optional[str] = None,
        model_name: Optional[str] = None,
        quality_measure: str = "skewness",
    ) -> dict:
        """
        Get mesh information using a stable Java tag.

        Args:
            mesh_name: Mesh sequence tag (default: first mesh)
            component_name: Component name (default: first component)
            model_name: Model name (default: current model)
            quality_measure: COMSOL element quality measure, e.g. skewness or volcircum

        Returns:
            Mesh tag, component, geometry, and feature information
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }

        try:
            comp = _get_component_java(model.java, component_name)
            mesh_tags = _mesh_tags(comp)
            if not mesh_tags:
                return {"success": False, "error": "No meshes defined in model."}

            target_tag = mesh_name or mesh_tags[0]
            if target_tag not in mesh_tags:
                return {
                    "success": False,
                    "error": f"Mesh not found: {target_tag}",
                    "available_meshes": mesh_tags,
                }

            java_mesh = comp.mesh(target_tag)
            features = []
            feature_list = java_mesh.feature()
            for feature_tag in feature_list.tags():
                feature = java_mesh.feature(str(feature_tag))
                feature_info = {"tag": str(feature.tag())}
                try:
                    feature_info["type"] = str(feature.getType())
                except Exception:
                    pass
                label = _safe_label(feature)
                if label:
                    feature_info["label"] = label
                features.append(feature_info)

            info = {
                "tag": target_tag,
                "component": str(comp.tag()),
                "features": features,
                "feature_count": len(features),
            }
            label = _safe_label(java_mesh)
            if label:
                info["label"] = label

            try:
                info["geometry"] = str(java_mesh.geom())
            except Exception:
                pass

            quality = {"measure": quality_measure, "available": False}
            try:
                stats = java_mesh.stat()
                quality["elements"] = int(stats.getNumElem())
                quality["vertices"] = int(stats.getNumVertex())
                if quality["elements"]:
                    stats.setQualityMeasure(quality_measure)
                    quality["minimum"] = float(stats.getMinQuality())
                    quality["mean"] = float(stats.getMeanQuality())
                    quality["available"] = True
                else:
                    quality["reason"] = "The mesh has no built elements."
            except Exception as exc:
                quality["reason"] = str(exc)
            info["quality"] = quality

            return {"success": True, "mesh": info}
        except Exception as e:
            return {"success": False, "error": f"Failed to get mesh info: {str(e)}"}
