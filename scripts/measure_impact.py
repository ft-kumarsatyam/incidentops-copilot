"""Print a reproducible pilot estimate; this is not a production benchmark."""

import argparse
import json


def calculate(manual_minutes: float, automated_minutes: float, incidents: int) -> dict:
    if manual_minutes <= 0 or automated_minutes < 0 or incidents <= 0:
        raise ValueError("minutes and incident count must be positive")
    saved_each = manual_minutes - automated_minutes
    return {
        "incidents": incidents,
        "manual_minutes_per_incident": manual_minutes,
        "automated_minutes_per_incident": automated_minutes,
        "minutes_saved_total": round(saved_each * incidents, 1),
        "time_reduction_percent": round(saved_each / manual_minutes * 100, 1),
        "manual_coordination_steps": 8,
        "automated_human_steps": 2,
        "step_reduction_percent": 75.0,
        "claim_scope": "pilot estimate; replace inputs with observed data",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manual-minutes", type=float, default=20)
    parser.add_argument("--automated-minutes", type=float, default=4)
    parser.add_argument("--incidents", type=int, default=25)
    args = parser.parse_args()
    result = calculate(args.manual_minutes, args.automated_minutes, args.incidents)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
