from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from buywell_edge_sdk.contracts import EDGE_PROTOCOL_VERSION


@dataclass
class ProtocolTranscript:
    authenticated: bool = False
    instances: dict[str, dict[str, Any]] = field(default_factory=dict)
    results: list[dict[str, Any]] = field(default_factory=list)

    def accept(self, message: dict[str, Any]) -> dict[str, Any] | None:
        kind = message.get("type")
        if kind == "authenticate":
            if message.get("protocolVersion") != EDGE_PROTOCOL_VERSION:
                raise ValueError("protocol_version_mismatch")
            for field_name in ("deviceId", "credential", "edgeVersion", "platform"):
                if not message.get(field_name):
                    raise ValueError(f"{field_name}_required")
            self.authenticated = True
            return {"type": "authenticated", "protocolVersion": EDGE_PROTOCOL_VERSION}
        if not self.authenticated:
            raise ValueError("authentication_required")
        if kind == "connection.snapshot":
            for instance in message.get("connections", []):
                self.instances[str(instance["connectionId"])] = dict(instance)
            return {"type": "connection.snapshot.accepted", "requestId": message.get("requestId")}
        if kind == "job.result":
            self.results.append(dict(message))
            return {"type": "job.result.accepted", "jobId": message.get("jobId"), "accepted": True}
        if kind == "heartbeat":
            return {"type": "heartbeat.accepted"}
        raise ValueError("message_unsupported")
