"""OpenAPI description of the existing platform API, used by the AgentCore Gateway target."""

from __future__ import annotations

from typing import Any


def _param(name: str, where: str, desc: str) -> dict[str, Any]:
    return {"name": name, "in": where, "required": True, "schema": {"type": "string"}, "description": desc}


def platform_openapi(base_url: str) -> dict[str, Any]:
    ok = {"200": {"description": "OK", "content": {"application/json": {"schema": {"type": "object"}}}}}
    return {
        "openapi": "3.0.1",
        "info": {"title": "Smart home platform API", "version": "1.0.0"},
        "servers": [{"url": base_url}],
        "paths": {
            "/members/{userId}/devices": {
                "get": {
                    "operationId": "getUserDevices",
                    "summary": "List the devices the user is allowed to control",
                    "parameters": [_param("userId", "path", "Platform user id")],
                    "responses": ok,
                }
            },
            "/devices/{uuid}/status": {
                "get": {
                    "operationId": "queryDeviceStatus",
                    "summary": "Get the current status of one device",
                    "parameters": [
                        _param("uuid", "path", "Device uuid"),
                        _param("userId", "query", "Platform user id"),
                    ],
                    "responses": ok,
                }
            },
            "/devices/{uuid}/commands": {
                "post": {
                    "operationId": "controlDevice",
                    "summary": "Send one standard command to a device through the device control service",
                    "parameters": [_param("uuid", "path", "Device uuid")],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["userId", "action"],
                                    "properties": {
                                        "userId": {"type": "string"},
                                        "action": {"type": "string"},
                                        "params": {"type": "object"},
                                        "requestId": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"202": {"description": "Accepted"}},
                }
            },
        },
    }
