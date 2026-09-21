#!/usr/bin/env python3

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts/ci/app_host_result_accounting.py"
SPEC = importlib.util.spec_from_file_location("app_host_result_accounting", SCRIPT)
accounting = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(accounting)


def test_enumeration_uses_built_bundle_identifiers() -> None:
    data = {
        "values": [
            {
                "name": "Test Plan",
                "children": [
                    {
                        "name": "cmuxTests",
                        "children": [
                            {
                                "name": "FooTests",
                                "children": [
                                    {"name": "testOne()"},
                                    {"name": "testTwo()"},
                                ],
                            },
                            {
                                "name": "Swift Display Suite",
                                "children": [
                                    {
                                        "name": "nested suite",
                                        "children": [
                                            {"name": "modern test"},
                                        ],
                                    }
                                ],
                            },
                        ],
                    }
                ],
            }
        ]
    }
    assert accounting.parse_enumeration(data) == {
        "FooTests/testOne()",
        "FooTests/testTwo()",
        "Swift Display Suite/nested suite/modern test",
    }


def test_enumeration_accepts_explicit_flat_identifiers() -> None:
    data = {
        "values": [
            {
                "children": [
                    {"identifier": "cmuxTests/FooTests/testOne()"},
                    {"identifier": "cmuxTests/ModernSuite/modern test"},
                ]
            }
        ]
    }
    assert accounting.parse_enumeration(data) == {
        "FooTests/testOne()",
        "ModernSuite/modern test",
    }


def test_typed_results_cover_xctest_and_swift_testing_the_same_way() -> None:
    data = {
        "testNodes": [
            {
                "nodeType": "Test Suite",
                "name": "FooTests",
                "result": "Failed",
                "children": [
                    {
                        "nodeType": "Test Case",
                        "name": "testLegacy()",
                        "nodeIdentifier": "FooTests/testLegacy()",
                        "result": "Failed",
                    },
                    {
                        "nodeType": "Test Case",
                        "name": "modern test",
                        "nodeIdentifier": "ModernSuite/modern test",
                        "result": "Passed",
                    },
                ],
            }
        ]
    }
    assert accounting.parse_xcresult_tests(data) == {
        "FooTests/testLegacy()": "Failed",
        "ModernSuite/modern test": "Passed",
    }


def test_known_failure_is_tolerated_but_new_failure_blocks() -> None:
    inventory = {"FooTests/testBad()", "BarTests/testGood()"}
    selectors = ["FooTests", "BarTests"]
    results = {
        "FooTests/testBad()": "Failed",
        "BarTests/testGood()": "Passed",
    }
    complete_log = "** TEST FAILED **\n"

    passed, messages = accounting.check_run(
        inventory=inventory,
        selectors=selectors,
        results=results,
        known={"FooTests/testBad()": {"classification": "test bug"}},
        log_text=complete_log,
        xcode_status=65,
    )
    assert passed is True
    assert any("RATCHET_KNOWN_FAILURE FooTests/testBad()" in line for line in messages)

    passed, messages = accounting.check_run(
        inventory=inventory,
        selectors=selectors,
        results=results,
        known={},
        log_text=complete_log,
        xcode_status=65,
    )
    assert passed is False
    assert "RATCHET_NEW_FAILURE FooTests/testBad()" in messages


def test_zero_matching_selector_never_passes() -> None:
    passed, messages = accounting.check_run(
        inventory={"FooTests/testOne()"},
        selectors=["MissingSuite"],
        results={"FooTests/testOne()": "Passed"},
        known={},
        log_text="** TEST SUCCEEDED **\n",
        xcode_status=0,
    )
    assert passed is False
    assert messages == ["selector matched zero built tests: MissingSuite"]


def test_zero_executed_selector_never_passes() -> None:
    passed, messages = accounting.check_run(
        inventory={"FooTests/testOne()", "BarTests/testTwo()"},
        selectors=["FooTests", "BarTests"],
        results={"FooTests/testOne()": "Passed"},
        known={},
        log_text="** TEST SUCCEEDED **\n",
        xcode_status=0,
    )
    assert passed is False
    assert messages == ["selector produced zero typed test results: BarTests"]


def test_restart_or_outer_timeout_is_never_ratcheted_green() -> None:
    for log in (
        "Restarting after unexpected exit, crash, or test timeout\n** TEST FAILED **\n",
        "xcodebuild unit-test batch 1/12 timeout after 900s; terminating\n",
        "Idle timed out after 300s (no test progress; app-host log lines do not count)\n",
    ):
        passed, messages = accounting.check_run(
            inventory={"FooTests/testBad()"},
            selectors=["FooTests"],
            results={"FooTests/testBad()": "Failed"},
            known={"FooTests/testBad()": {"classification": "timeout/hang"}},
            log_text=log,
            xcode_status=65,
        )
        assert passed is False
        assert messages[0].startswith("incomplete app-host run:")


def test_catalog_may_only_shrink() -> None:
    old = {
        "FooTests/testOne()": {"classification": "product bug"},
        "BarTests/testTwo()": {"classification": "test bug"},
    }
    new = {"BarTests/testTwo()": {"classification": "test bug"}}
    assert set(new).issubset(old)
    assert not set(old).issubset(new)


def test_nonempty_catalog_requires_exact_bootstrap_main_sha() -> None:
    data = {
        "bootstrap_main_sha": None,
        "version": 1,
        "tests": {
            "FooTests/testOne()": {
                "classification": "unknown",
            }
        },
    }
    try:
        accounting.validate_catalog(data)
    except ValueError as error:
        assert "bootstrap_main_sha" in str(error)
    else:
        raise AssertionError("non-empty catalog without exact main SHA was accepted")


def test_catalog_requires_campaign_classification() -> None:
    data = {
        "bootstrap_main_sha": "0123456789abcdef0123456789abcdef01234567",
        "version": 1,
        "tests": {
            "FooTests/testOne()": {
                "classification": "host/display dependency",
                "issue": 123,
            }
        },
    }
    parsed = accounting.validate_catalog(data)
    assert set(parsed) == {"FooTests/testOne()"}

    bad = json.loads(json.dumps(data))
    bad["tests"]["FooTests/testOne()"]["classification"] = "flaky"
    try:
        accounting.validate_catalog(bad)
    except ValueError as error:
        assert "invalid classification" in str(error)
    else:
        raise AssertionError("invalid classification was accepted")


if __name__ == "__main__":
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
    print("ok")
