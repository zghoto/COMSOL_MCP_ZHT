"""Session management tools for COMSOL MCP Server."""

import os
from typing import Optional

import anyio
from mcp.server.fastmcp import FastMCP
import mph
import mph.session as mph_session


class SessionManager:
    """Singleton manager for COMSOL client session."""
    
    _instance: Optional["SessionManager"] = None
    _client: Optional[mph.Client] = None
    _shared_client: Optional[mph.Client] = None
    _connection_error: Optional[str] = None
    _models: dict[str, mph.Model] = {}
    _current_model: Optional[str] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @property
    def client(self) -> Optional[mph.Client]:
        return self._client
    
    @property
    def is_connected(self) -> bool:
        return self._client is not None

    @property
    def is_shared(self) -> bool:
        return self._client is not None and not self._client.standalone
    
    @property
    def current_model(self) -> Optional[str]:
        return self._current_model
    
    @property
    def models(self) -> dict[str, mph.Model]:
        if self.is_shared:
            return {str(model.java.tag()): model for model in self._client.models()}
        return self._models.copy()
    
    def start(self, cores: Optional[int] = None, version: Optional[str] = None, products: Optional[list[str]] = None) -> dict:
        """Start a COMSOL client session."""
        # Several COMSOL installations may coexist; COMSOL_MCP_VERSION
        # selects the default back-end when no version is given.
        version = version or os.environ.get("COMSOL_MCP_VERSION") or None
        port = os.environ.get("COMSOL_MCP_SERVER_PORT", "").strip()
        if port:
            try:
                server_port = int(port)
            except ValueError:
                return self._connection_failed("COMSOL_MCP_SERVER_PORT must be an integer from 1 to 65535.")
            host = os.environ.get("COMSOL_MCP_SERVER_HOST", "localhost")
            return self.connect(port=server_port, host=host, version=version)
        if self.is_shared:
            return self.connect(port=self._client.port, host=self._client.host, version=version)
        if self._shared_client is not None:
            return self._connection_failed("Use comsol_connect to reconnect to the server, or restart MCP for a standalone session.")
        if self._client is not None:
            try:
                self._client.clear()
                self._models.clear()
                self._current_model = None
                return {
                    "success": True,
                    "version": self._client.version,
                    "cores": self._client.cores,
                    "standalone": self._client.standalone,
                    "message": "Cleared existing session and ready."
                }
            except Exception as e:
                self._client = None
                self._models.clear()
                self._current_model = None
        if self._client is None:
            try:
                if mph_session.client is not None:
                    self._client = mph_session.client
                    return {
                        "success": True,
                        "version": self._client.version,
                        "cores": self._client.cores,
                        "standalone": self._client.standalone,
                        "message": "Reused existing client from MPh session."
                    }
            except Exception:
                pass
            try:
                kwargs = {"cores": cores, "version": version}
                if products:
                    kwargs["products"] = products
                self._client = mph.Client(**{k: v for k, v in kwargs.items() if v is not None})
                return {
                    "success": True,
                    "version": self._client.version,
                    "cores": self._client.cores,
                    "standalone": self._client.standalone,
                }
            except Exception as e:
                return {"success": False, "error": str(e)}
    
    def _connection_failed(self, message: str) -> dict:
        self._connection_error = message
        return {"success": False, "error": message}

    def connect(self, port: int, host: str = "localhost", version: Optional[str] = None) -> dict:
        """Connect to an existing COMSOL Multiphysics Server."""
        host = host.strip()
        if not host or not 1 <= port <= 65535:
            return self._connection_failed("Specify a COMSOL Server host and a port from 1 to 65535.")
        version = version or os.environ.get("COMSOL_MCP_VERSION") or None
        username = os.environ.get("COMSOL_MCP_SERVER_USERNAME")
        password = os.environ.get("COMSOL_MCP_SERVER_PASSWORD")
        if bool(username) != bool(password):
            return self._connection_failed("Set both COMSOL_MCP_SERVER_USERNAME and COMSOL_MCP_SERVER_PASSWORD, or leave both unset to use COMSOL's saved login.")

        client = self._client or self._shared_client or mph_session.client
        try:
            if client is None:
                # Keep the initialized client even if the first login fails. JPype
                # cannot create a second JVM in this process.
                client = mph.Client(port=port, host=None, version=version)
                self._shared_client = client
            if client.standalone:
                return self._connection_failed("Restart MCP with COMSOL_MCP_SERVER_PORT set to switch from a standalone session.")
            if version and version != client.version:
                return self._connection_failed(f"Restart MCP to change the COMSOL client version from {client.version} to {version}.")
            if client.port is not None and (client.host, client.port) != (host, port):
                return self._connection_failed("Already connected to a different COMSOL Server. Disconnect first.")

            self._shared_client = client
            if client.port is None:
                if username and password:
                    # MPh's connect method uses the saved COMSOL login. Keep its
                    # connection fields in sync when using the credential overload.
                    client.java.connect(host, port, username, password)
                    client.host = host
                    client.port = port
                else:
                    client.connect(port, host)
        except Exception as exc:
            message = str(exc)
            if password:
                message = message.replace(password, "[redacted]")
            return self._connection_failed(message)

        self._client = client
        self._connection_error = None
        return {"success": True, "version": client.version, "host": client.host,
                "port": client.port, "standalone": False}
    
    def disconnect(self) -> dict:
        """Release a shared connection or clear the standalone session."""
        if self._client is None:
            return {"success": True, "message": "No active session."}
        if self.is_shared:
            try:
                self._client.disconnect()
            except Exception as exc:
                return {"success": False, "error": str(exc)}
            self._client = None
            self._models.clear()
            self._current_model = None
            return {"success": True, "message": "Disconnected from the shared COMSOL Server."}
        try:
            self._client.clear()
        except Exception:
            pass
        try:
            self._client = None
        except Exception:
            pass
        self._models.clear()
        self._current_model = None
        try:
            mph_session.client = None
            mph_session.server = None
            mph_session.thread = None
        except Exception:
            pass
        return {"success": True, "message": "Session disconnected and client destroyed."}
    
    def get_status(self) -> dict:
        """Get current session status."""
        if self._client is None:
            status = {
                "connected": False,
                "message": "No active COMSOL session."
            }
            if self._connection_error:
                status["error"] = self._connection_error
            return status

        status = {
            "connected": True,
            "version": self._client.version,
            "cores": self._client.cores,
            "standalone": not self.is_shared,
        }
        if self.is_shared:
            status["host"] = self._client.host
            status["port"] = self._client.port
            models = self.models
            status["models"] = [{"name": model.name(), "tag": tag} for tag, model in models.items()]
            if self._current_model not in models:
                self._current_model = None
        else:
            status["models"] = []
            for name in self._client.names():
                model_info = {"name": name}
                if name in self._models:
                    model = self._models[name]
                    model_info["file"] = model.file() if hasattr(model, 'file') else None
                status["models"].append(model_info)
        status["current_model"] = self._current_model
        return status
    
    def add_model(self, model: mph.Model, set_current: bool = False) -> str:
        """Track a model by its tag on a shared server or by name in standalone mode."""
        if self.is_shared:
            key = str(model.java.tag())
        else:
            key = model.name()
            self._models[key] = model
        if set_current or self._current_model is None:
            self._current_model = key
        return key
    
    def get_model(self, name: Optional[str] = None) -> Optional[mph.Model]:
        """Resolve the current model, a server tag, or an unambiguous model name."""
        key = self._current_model if name is None else name
        if key is None:
            return None
        if not self.is_shared:
            return self._models.get(key)
        if key in self._client.java.tags():
            return mph.Model(self._client.java.model(key))
        if name is None:
            self._current_model = None
            return None
        matches = [model for model in self._client.models() if model.name() == name]
        if len(matches) > 1:
            raise ValueError(f"Multiple models are named {name!r}. Use a model tag from model_list.")
        return next(iter(matches), None)
    
    def set_current_model(self, name: str) -> bool:
        """Set the current active model."""
        model = self.get_model(name)
        if model is None:
            return False
        self._current_model = str(model.java.tag()) if self.is_shared else name
        return True
    
    def remove_model(self, name: str) -> bool:
        """Remove a model from tracking and client."""
        if self._client is None:
            return False
        if self.is_shared:
            model = self.get_model(name)
            if model is None:
                return False
            key = str(model.java.tag())
            self._client.remove(model)
            if self._current_model == key:
                self._current_model = None
            return True
        if name in self._models:
            try:
                self._client.remove(self._models[name])
                del self._models[name]
                if self._current_model == name:
                    self._current_model = next(iter(self._models.keys()), None)
                return True
            except Exception:
                pass
        return False


session_manager = SessionManager()


def register_session_tools(mcp: FastMCP) -> None:
    """Register session management tools with the MCP server."""
    lifecycle = anyio.CapacityLimiter(1)
    
    @mcp.tool()
    async def comsol_start(cores: Optional[int] = None, version: Optional[str] = None, products: Optional[list[str]] = None) -> dict:
        """
        Start a local COMSOL session or connect to the configured shared server.
        
        Args:
            cores: Number of processor cores to use (default: all available)
            version: COMSOL version to use, e.g., '6.3' (default: $COMSOL_MCP_VERSION, else latest installed)
            products: List of COMSOL products to load, e.g., ["ACDC"], ["ACDC", "CADImport"]
                     (default: all available products)
        
        Returns:
            Session info including version and core count, or error message
        """
        # Starting the JVM blocks; run it in a worker thread so the event loop stays responsive
        return await anyio.to_thread.run_sync(
            lambda: session_manager.start(cores=cores, version=version, products=products), limiter=lifecycle
        )
    
    @mcp.tool()
    async def comsol_connect(port: int, host: str = "localhost") -> dict:
        """
        Connect to an existing COMSOL Multiphysics Server for shared modeling.
        
        Args:
            port: Port number the COMSOL server is listening on
            host: Server hostname or IP address (default: 'localhost')
        
        Returns:
            Connection info or error message
        """
        # Same as comsol_start: constructing the Client starts the JVM
        return await anyio.to_thread.run_sync(
            lambda: session_manager.connect(port=port, host=host), limiter=lifecycle
        )
    
    @mcp.tool()
    async def comsol_disconnect() -> dict:
        """
        Release a shared COMSOL Server connection or clear the standalone session.
        
        Returns:
            Success status and message
        """
        async with lifecycle:
            return session_manager.disconnect()
    
    @mcp.tool()
    def comsol_status() -> dict:
        """
        Get the current COMSOL session status.
        
        Returns:
            Session information including connection status, version, and loaded models
        """
        return session_manager.get_status()
