# SPDX-License-Identifier: GPL-3.0-or-later
"""
Conditional formatting for turing-smart-screen-python.

Evaluates CASE/WHEN/ELSE blocks in theme YAML to enable dynamic
widget properties based on current sensor values.
"""

import re
from typing import Any, Dict, List, Optional


def get_sensor_value(sensor_name: str) -> Optional[Any]:
    """
    Get current value from a sensor by class name.

    Args:
        sensor_name: Sensor class name (e.g., 'Gpu0_Temp', 'Cpu_Power')

    Returns:
        Sensor value (numeric or string), or None if not found
    """
    try:
        import library.sensors.sensors_custom as sensors_custom
        sensor_class = getattr(sensors_custom, sensor_name, None)
        if sensor_class:
            sensor = sensor_class()
            # Try as_numeric first, fall back to as_string
            value = sensor.as_numeric()
            if value is None:
                value = sensor.as_string()
            return value
    except Exception:
        pass
    return None


def evaluate_condition(condition: str) -> bool:
    """
    Evaluate a condition string, substituting sensor values.

    Args:
        condition: Python expression like "Gpu0_Temp >= 85"

    Returns:
        Boolean result of evaluation
    """
    try:
        expr = condition

        # Find sensor names (CamelCase with optional underscores)
        # Matches: Gpu0_Temp, Cpu_Power, Bw_NetRx, etc.
        # Requires at least one lowercase letter after initial capital
        # to avoid matching string literals like 'P8'
        sensor_pattern = r'\b([A-Z][a-z]+[a-z0-9]*(?:_[A-Za-z][A-Za-z0-9]*)*)\b'
        sensor_names = re.findall(sensor_pattern, condition)

        # Deduplicate while preserving order
        seen = set()
        unique_names = []
        for name in sensor_names:
            if name not in seen:
                seen.add(name)
                unique_names.append(name)

        # Substitute sensor values
        for name in unique_names:
            value = get_sensor_value(name)
            if value is not None:
                if isinstance(value, str):
                    expr = expr.replace(name, repr(value))
                else:
                    expr = expr.replace(name, str(value))

        # Safe eval with no builtins
        return bool(eval(expr, {"__builtins__": {}}, {}))

    except Exception:
        return False


def evaluate_case(case_block: List[Dict]) -> Dict:
    """
    Evaluate CASE block and return matching overrides.

    Args:
        case_block: List of WHEN/ELSE conditions from theme YAML

    Returns:
        Dict of property overrides from matched case, or {}
    """
    for case in case_block:
        # Handle ELSE (must be last, catches everything)
        if 'ELSE' in case:
            # Return all properties except 'ELSE' key
            return {k: v for k, v in case.items() if k != 'ELSE'}

        # Handle WHEN condition
        condition = case.get('WHEN', '')
        if condition and evaluate_condition(condition):
            # Return all properties except 'WHEN' key
            return {k: v for k, v in case.items() if k != 'WHEN'}

    return {}


def apply_case(base_props: Dict) -> Dict:
    """
    Apply CASE overrides to base properties.

    Args:
        base_props: Original widget properties from theme YAML

    Returns:
        Merged properties with CASE overrides applied
    """
    case_block = base_props.get('CASE')
    if not case_block:
        return base_props

    # Get matching overrides
    overrides = evaluate_case(case_block)

    # Merge: base properties + overrides (overrides win)
    result = {k: v for k, v in base_props.items() if k != 'CASE'}
    result.update(overrides)

    return result
