from evals.compare_architectures import ArchitectureResult, recommendation


def result(name: str, passed: bool, roles: int) -> ArchitectureResult:
    return ArchitectureResult(
        name=name,
        roles=roles,
        case_checks=[{"status": passed, "safety": True, "grounded": True}],
        latency_ms=[1.0],
    )


def test_equal_quality_rejects_specialist_complexity() -> None:
    adopt, reason = recommendation(
        result("current", True, 2),
        result("specialists", True, 5),
    )

    assert adopt is False
    assert "No quality gain" in reason


def test_material_safe_quality_gain_adopts_specialists() -> None:
    baseline = ArchitectureResult(
        name="current",
        roles=2,
        case_checks=[
            {"status": True, "safety": True, "grounded": True},
            {"status": False, "safety": True, "grounded": True},
        ],
        latency_ms=[1.0, 1.0],
    )
    specialists = ArchitectureResult(
        name="specialists",
        roles=5,
        case_checks=[
            {"status": True, "safety": True, "grounded": True},
            {"status": True, "safety": True, "grounded": True},
        ],
        latency_ms=[2.0, 2.0],
    )

    assert recommendation(baseline, specialists)[0] is True
