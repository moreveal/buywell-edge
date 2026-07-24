import pytest

from buywell_edge.testing import ProtocolTranscript


def test_protocol_requires_authentication_and_routes_instances():
    transcript = ProtocolTranscript()
    with pytest.raises(ValueError, match="authentication_required"):
        transcript.accept({"type": "heartbeat"})
    response = transcript.accept({
        "type": "authenticate",
        "protocolVersion": "2.0.0",
        "deviceId": "2fb6e584-a961-42de-9f76-077994f91975",
        "credential": "x" * 40,
        "edgeVersion": "0.1.0",
        "platform": "linux-x86_64",
    })
    assert response["type"] == "authenticated"
    transcript.accept({
        "type": "connection.snapshot",
        "requestId": "1",
        "connections": [{
            "connectionId": "6f503381-9581-4936-b0c0-93189ba18da8",
            "extensionId": "example.market",
            "extensionVersion": "1.0.0",
            "packageDigest": "a" * 64,
            "enabled": True,
            "health": {"state": "healthy"},
        }],
    })
    assert len(transcript.instances) == 1
