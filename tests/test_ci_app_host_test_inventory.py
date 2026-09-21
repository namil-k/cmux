import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci/app_host_test_inventory.py"
SPEC = importlib.util.spec_from_file_location("app_host_test_inventory", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def document(enabled=(), disabled=(), errors=()):
    return {
        "errors": list(errors),
        "values": [{
            "testPlan": "cmux-unit",
            "enabledTests": [{"identifier": x} for x in enabled],
            "disabledTests": [{"identifier": x} for x in disabled],
        }],
    }


def test_normalizes_flat_built_inventory() -> None:
    result = MODULE.normalize(
        document(
            enabled=[
                "cmuxTests/ExampleTests/testOne",
                "cmuxTests/ExampleTests/testTwo",
                "cmuxTests/SwiftSuite/a parameterized test()",
            ],
            disabled=["cmuxTests/SkippedTests/testLater"],
        ),
        required_target="cmuxTests",
    )

    assert result["enabled_test_count"] == 3
    assert result["disabled_test_count"] == 1
    assert result["suite_count"] == 2
    assert result["enabled_tests_by_suite"] == {
        "cmuxTests/ExampleTests": 2,
        "cmuxTests/SwiftSuite": 1,
    }
    assert result["test_plans"] == ["cmux-unit"]


def test_rejects_zero_enabled_tests() -> None:
    try:
        MODULE.normalize(document(), required_target="cmuxTests")
    except ValueError as exc:
        assert "zero enabled tests" in str(exc)
    else:
        raise AssertionError("empty enumeration must fail")


def test_rejects_enumeration_errors() -> None:
    try:
        MODULE.normalize(document(errors=[{"message": "bundle failed to load"}]), required_target="cmuxTests")
    except ValueError as exc:
        assert "bundle failed to load" in str(exc)
    else:
        raise AssertionError("enumeration errors must fail")


def test_rejects_other_test_targets() -> None:
    try:
        MODULE.normalize(
            document(enabled=["cmuxTests/A/testA", "cmuxUITests/B/testB"]),
            required_target="cmuxTests",
        )
    except ValueError as exc:
        assert "outside cmuxTests" in str(exc)
    else:
        raise AssertionError("mixed targets must fail")


def test_cli_writes_reproducible_json(tmp_path: Path) -> None:
    source = tmp_path / "raw.json"
    output = tmp_path / "normalized.json"
    source.write_text(
        json.dumps(document(enabled=["cmuxTests/Z/testB", "cmuxTests/A/testA"])),
        encoding="utf-8",
    )

    normalized = MODULE.normalize(MODULE.load_inventory(source), required_target="cmuxTests")
    output.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    parsed = json.loads(output.read_text(encoding="utf-8"))
    assert parsed["enabled_tests"] == ["cmuxTests/A/testA", "cmuxTests/Z/testB"]
