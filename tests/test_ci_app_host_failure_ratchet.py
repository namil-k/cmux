import importlib.util
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci/app_host_failure_ratchet.py"
SPEC = importlib.util.spec_from_file_location("app_host_failure_ratchet", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

BASELINE = "1" * 40


def catalog(*ids: tuple[str, str]) -> dict:
    return MODULE.load_catalog_data({
        "version": 1,
        "baseline_sha": BASELINE,
        "failures": [
            {
                "id": identifier,
                "framework": framework,
                "category": "stale test",
                "issue": 8565,
                "first_seen_run": 100,
                "last_confirmed_run": 200,
            }
            for identifier, framework in ids
        ],
    })


def xctest_failure(identifier: str) -> str:
    suite, test = identifier.removeprefix("xctest:").split("/", 1)
    return (
        f"Test Case '-[{suite} {test}]' started.\n"
        f"/tmp/Test.swift:1: error: -[{suite} {test}] : XCTAssertEqual failed\n"
        f"Test Case '-[{suite} {test}]' failed (0.1 seconds).\n"
        "Executed 7 tests, with 1 failure (0 unexpected)\n"
    )


def swift_failure(name: str) -> str:
    return (
        "◇ Test run started.\n"
        f"◇ Test {name} started.\n"
        f"✘ Test {name} recorded an issue at ExampleTests.swift:12:5: Expectation failed\n"
        f"✘ Test {name} failed after 0.1 seconds with 1 issue.\n"
        "✘ Test run with 9 tests failed after 1.0 seconds with 1 issue.\n"
    )


def test_known_xctest_failure_is_tolerated() -> None:
    identifier = "xctest:cmuxTests.ExampleTests/testExample"
    passed, message = MODULE.evaluate(
        xctest_failure(identifier),
        exit_code=65,
        catalog=catalog((identifier, "xctest")),
    )
    assert passed
    assert identifier in message


def test_new_xctest_failure_blocks() -> None:
    identifier = "xctest:cmuxTests.ExampleTests/testNew"
    passed, message = MODULE.evaluate(
        xctest_failure(identifier),
        exit_code=65,
        catalog=catalog(),
    )
    assert not passed
    assert "new app-host failure" in message


def test_known_swift_testing_failure_is_tolerated() -> None:
    name = "parameterizedThing(value: 3)"
    identifier = f"swift:{name}"
    passed, message = MODULE.evaluate(
        swift_failure(name),
        exit_code=65,
        catalog=catalog((identifier, "swift-testing")),
    )
    assert passed
    assert identifier in message


def test_zero_matching_tests_never_passes() -> None:
    passed, message = MODULE.evaluate(
        "Executed 0 tests, with 0 failures (0 unexpected)\n",
        exit_code=0,
        catalog=catalog(),
    )
    assert not passed
    assert "zero executed tests" in message


def test_timeout_and_crash_markers_stay_hard_failures() -> None:
    identifier = "xctest:cmuxTests.ExampleTests/testExample"
    known = catalog((identifier, "xctest"))
    for extra in (
        "Restarting after unexpected exit, crash, or test timeout; summary follows.\n",
        "✘ Test thing() recorded an issue: Time limit was exceeded: 300.000 seconds\n",
        "Failed to establish communication with the test runner\n",
    ):
        passed, message = MODULE.evaluate(
            xctest_failure(identifier) + extra,
            exit_code=65,
            catalog=known,
        )
        assert not passed
        assert "hard app-host failure" in message


def test_failed_summary_without_identifier_blocks() -> None:
    passed, message = MODULE.evaluate(
        "Executed 2 tests, with 1 failure (0 unexpected)\n",
        exit_code=65,
        catalog=catalog(),
    )
    assert not passed
    assert "no typed test identifier" in message


def test_passing_summaries_require_zero_xcodebuild_status() -> None:
    output = "Executed 4 tests, with 0 failures (0 unexpected)\n"
    assert MODULE.evaluate(output, exit_code=0, catalog=catalog())[0]
    passed, message = MODULE.evaluate(output, exit_code=65, catalog=catalog())
    assert not passed
    assert "exited 65" in message


def test_catalog_rejects_duplicate_ids() -> None:
    identifier = "xctest:cmuxTests.ExampleTests/testExample"
    value = {
        "version": 1,
        "baseline_sha": BASELINE,
        "failures": [
            {
                "id": identifier,
                "framework": "xctest",
                "category": "stale test",
                "issue": 1,
                "first_seen_run": 1,
                "last_confirmed_run": 1,
            },
            {
                "id": identifier,
                "framework": "xctest",
                "category": "stale test",
                "issue": 1,
                "first_seen_run": 1,
                "last_confirmed_run": 1,
            },
        ],
    }
    try:
        MODULE.load_catalog_data(value)
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate catalog ids must fail")


def test_existing_catalog_may_only_shrink() -> None:
    a = ("xctest:cmuxTests.A/testA", "xctest")
    b = ("swift:testB()", "swift-testing")
    base = catalog(a, b)
    smaller = catalog(a)
    passed, message = MODULE.check_shrink_only(smaller, base, base_ref="origin/main")
    assert passed
    assert "shrank" in message

    larger = catalog(a, b)
    passed, message = MODULE.check_shrink_only(larger, catalog(a), base_ref="origin/main")
    assert not passed
    assert "may only shrink" in message


def test_initial_catalog_requires_baseline_ancestor() -> None:
    current = catalog()
    completed = type("Completed", (), {"returncode": 0})()
    with patch.object(MODULE.subprocess, "run", return_value=completed) as run:
        passed, _ = MODULE.check_shrink_only(current, None, base_ref="origin/main")
    assert passed
    assert run.call_args.args[0][:3] == ["git", "merge-base", "--is-ancestor"]
