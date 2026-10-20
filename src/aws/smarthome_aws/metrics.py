"""CloudWatch Embedded Metric Format (EMF) emitter, stdlib only."""

from __future__ import annotations

import json
import os
import sys
import time


class EmfMetrics:
    def __init__(self, namespace: str | None = None):
        self.namespace = namespace or os.environ.get("METRICS_NAMESPACE", "SmartHomeNL")
        self._values: dict[str, tuple[float, str]] = {}

    def put(self, name: str, value: float, unit: str = "Milliseconds") -> None:
        prev = self._values.get(name)
        if prev and unit == "Count":
            value += prev[0]
        self._values[name] = (float(value), unit)

    def flush(self, dimensions: dict[str, str] | None = None) -> None:
        if not self._values:
            return
        dims = {"Service": "orchestrator", **(dimensions or {})}
        doc = {
            "_aws": {
                "Timestamp": int(time.time() * 1000),
                "CloudWatchMetrics": [
                    {
                        "Namespace": self.namespace,
                        # Publish once with all dimensions and once with Service only.
                        "Dimensions": [list(dims), ["Service"]],
                        "Metrics": [{"Name": n, "Unit": u} for n, (_, u) in self._values.items()],
                    }
                ],
            },
            **dims,
            **{n: v for n, (v, _) in self._values.items()},
        }
        sys.stdout.write(json.dumps(doc) + "\n")
        sys.stdout.flush()
        self._values = {}
