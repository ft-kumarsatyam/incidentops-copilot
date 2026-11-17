import importlib.util
from pathlib import Path


def test_measurement_is_reproducible() -> None:
    path = Path(__file__).parents[2] / "scripts" / "measure_impact.py"
    spec = importlib.util.spec_from_file_location("measure_impact", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    result = module.calculate(20, 4, 25)
    assert result["time_reduction_percent"] == 80.0
    assert result["minutes_saved_total"] == 400
