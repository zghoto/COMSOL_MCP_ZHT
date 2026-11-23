"""Regression tests for shared COMSOL sessions without a JVM."""

from unittest.mock import Mock

import pytest

from src.tools import session


@pytest.fixture
def shared_session(monkeypatch):
    manager = session.session_manager
    monkeypatch.setattr(manager, "_client", None)
    monkeypatch.setattr(manager, "_shared_client", None)
    monkeypatch.setattr(manager, "_connection_error", None)
    monkeypatch.setattr(manager, "_models", {})
    monkeypatch.setattr(manager, "_current_model", None)
    monkeypatch.setattr(session.mph_session, "client", None)
    monkeypatch.setenv("COMSOL_MCP_VERSION", "6.4")
    monkeypatch.setenv("COMSOL_MCP_SERVER_PORT", "2036")
    monkeypatch.setenv("COMSOL_MCP_SERVER_HOST", "localhost")
    monkeypatch.delenv("COMSOL_MCP_SERVER_USERNAME", raising=False)
    monkeypatch.delenv("COMSOL_MCP_SERVER_PASSWORD", raising=False)

    client = Mock(standalone=False, version="6.4", cores=1, host=None, port=None)
    client.models.return_value = []

    def connect(port, host):
        client.port, client.host = port, host

    def disconnect():
        client.port, client.host = None, None

    client.connect.side_effect = connect
    client.disconnect.side_effect = disconnect
    constructor = Mock(return_value=client)
    monkeypatch.setattr(session.mph, "Client", constructor)
    return manager, client, constructor


def test_shared_start_disconnect_and_reconnect(shared_session, monkeypatch):
    manager, client, constructor = shared_session
    monkeypatch.setenv("COMSOL_MCP_SERVER_USERNAME", "test-user")
    monkeypatch.setenv("COMSOL_MCP_SERVER_PASSWORD", "test-password")

    assert manager.start()["standalone"] is False
    assert manager.start()["port"] == 2036
    constructor.assert_called_once_with(port=2036, host=None, version="6.4")
    client.java.connect.assert_called_once_with("localhost", 2036, "test-user", "test-password")
    assert manager.connect(2037)["success"] is False
    assert manager.disconnect()["success"] is True
    assert manager.get_status()["connected"] is False
    assert manager.start()["success"] is True
    assert constructor.call_count == 1
    assert client.java.connect.call_count == 2
    client.disconnect.assert_called_once()
    client.clear.assert_not_called()


def test_failed_shared_login_retries_on_the_same_jvm(shared_session):
    manager, client, constructor = shared_session
    connect = client.connect.side_effect
    client.connect.side_effect = OSError("Connection refused")
    assert manager.start()["success"] is False
    assert manager.get_status()["error"] == "Connection refused"

    client.connect.side_effect = connect
    assert manager.start()["success"] is True
    assert constructor.call_count == 1
    assert manager.get_status()["port"] == 2036


def test_shared_tag_survives_rename_and_disambiguates_names(shared_session, monkeypatch):
    manager, client, _ = shared_session
    first, second = Mock(), Mock()
    first.java.tag.return_value = "Model1"
    second.java.tag.return_value = "Model2"
    first.name.return_value = second.name.return_value = "Desktop model"
    client.models.return_value = [first, second]
    client.java.tags.return_value = ["Model1", "Model2"]
    client.java.model.side_effect = {"Model1": first, "Model2": second}.__getitem__
    monkeypatch.setattr(session.mph, "Model", lambda model: model)

    assert manager.start()["success"] is True
    assert list(manager.models) == ["Model1", "Model2"]
    with pytest.raises(ValueError, match="Use a model tag"):
        manager.set_current_model("Desktop model")
    assert manager.set_current_model("Model2")
    second.name.return_value = "Renamed in Desktop"
    assert manager.get_model() is second
    assert manager.get_status()["current_model"] == "Model2"

    client.models.return_value = [first]
    client.java.tags.return_value = ["Model1"]
    assert manager.get_model() is None
    assert manager.current_model is None


@pytest.mark.parametrize("port", ["abc", "0", "65536"])
def test_invalid_shared_port_does_not_start_standalone(shared_session, monkeypatch, port):
    manager, _, constructor = shared_session
    monkeypatch.setenv("COMSOL_MCP_SERVER_PORT", port)
    assert manager.start()["success"] is False
    assert manager.get_status()["connected"] is False
    constructor.assert_not_called()
