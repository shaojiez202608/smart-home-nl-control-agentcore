"""Device-type command catalog.

The catalog is the single source of truth for which actions a device type
supports and which parameters each action accepts. It is shared by the
simulated platform (to execute commands), by the orchestrator (to validate
model output) and by the agent (to describe capabilities to the model).

In a real deployment this information comes from the platform's device
capability model; the shape here intentionally mirrors a typical
"thing model" (actions + typed, bounded parameters).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ParamSpec:
    name: str
    type: str  # "int" | "enum"
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] = ()
    unit: str = ""

    def check(self, value: Any) -> str | None:
        """Return an error string if ``value`` is not acceptable, else None."""
        if self.type == "int":
            # bool is a subclass of int; reject it explicitly.
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return f"{self.name} must be a number"
            if float(value) != int(value):
                return f"{self.name} must be an integer"
            if self.minimum is not None and value < self.minimum:
                return f"{self.name} below minimum {self.minimum}"
            if self.maximum is not None and value > self.maximum:
                return f"{self.name} above maximum {self.maximum}"
            return None
        if self.type == "enum":
            if value not in self.choices:
                return f"{self.name} must be one of {list(self.choices)}"
            return None
        return f"unsupported parameter type {self.type}"

    def describe(self) -> str:
        if self.type == "int":
            return f"{self.name}:int[{self.minimum}-{self.maximum}]{self.unit}"
        return f"{self.name}:{'|'.join(self.choices)}"


@dataclass(frozen=True)
class ActionSpec:
    name: str
    params: tuple[ParamSpec, ...] = ()
    # Target state fragment applied by a successful execution. Parameter
    # values are merged in by the executor.
    effect: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        if not self.params:
            return self.name
        return f"{self.name}({', '.join(p.describe() for p in self.params)})"


def _a(name: str, *params: ParamSpec, **effect: Any) -> ActionSpec:
    return ActionSpec(name=name, params=tuple(params), effect=effect)


BRIGHTNESS = ParamSpec("brightness", "int", 1, 100, unit="%")
COLOR_TEMP = ParamSpec("kelvin", "int", 2700, 6500, unit="K")
POSITION = ParamSpec("position", "int", 0, 100, unit="%")
CELSIUS = ParamSpec("celsius", "int", 16, 30, unit="C")
AC_MODE = ParamSpec("mode", "enum", choices=("cool", "heat", "auto", "fan"))
VOLUME = ParamSpec("volume", "int", 0, 100, unit="%")
ARM_MODE = ParamSpec("mode", "enum", choices=("home", "away"))

CATALOG: dict[str, dict[str, ActionSpec]] = {
    "light": {
        a.name: a
        for a in (
            _a("turnOn", power="on"),
            _a("turnOff", power="off"),
            _a("setBrightness", BRIGHTNESS, power="on"),
            _a("setColorTemperature", COLOR_TEMP, power="on"),
        )
    },
    "curtain": {
        a.name: a
        for a in (
            _a("open", position=100),
            _a("close", position=0),
            _a("setPosition", POSITION),
        )
    },
    "lock": {
        a.name: a
        for a in (
            _a("lock", locked=True),
            _a("unlock", locked=False),
        )
    },
    "air_conditioner": {
        a.name: a
        for a in (
            _a("turnOn", power="on"),
            _a("turnOff", power="off"),
            _a("setTemperature", CELSIUS, power="on"),
            _a("setMode", AC_MODE, power="on"),
        )
    },
    "security_system": {
        a.name: a
        for a in (
            _a("arm", ARM_MODE, armed=True),
            _a("disarm", armed=False),
        )
    },
    "speaker": {
        a.name: a
        for a in (
            _a("play", playing=True),
            _a("pause", playing=False),
            _a("setVolume", VOLUME),
        )
    },
}

# Parameters that represent a user's taste and are therefore worth
# remembering as long-term preferences (see memory_policy.py). Plain on/off
# and positional actions are deliberately excluded.
PREFERENCE_PARAMS = frozenset({"brightness", "kelvin", "celsius", "volume"})


def actions_for(device_type: str) -> dict[str, ActionSpec]:
    return CATALOG.get(device_type, {})


def capability_summary(device_type: str) -> list[str]:
    return [a.describe() for a in actions_for(device_type).values()]
