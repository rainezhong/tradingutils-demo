"""Test subcommands for the trading CLI.

Consolidates all test entry points:
- pytest unit tests
- arb algorithm tests (from scripts/test_arb.py)
- integration E2E tests
- spread detector tests
"""

import subprocess
import sys
import os
from pathlib import Path

import click


# Get project root
PROJECT_ROOT = Path(__file__).parent.parent.parent


def run_pytest(test_path: str, extra_args: list = None):
    """Run pytest with given path and arguments."""
    cmd = [sys.executable, "-m", "pytest", test_path, "-v", "--tb=short"]
    if extra_args:
        cmd.extend(extra_args)

    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    sys.exit(result.returncode)


def run_test_arb(command: str = None):
    """Run arb test script with optional command."""
    script_path = PROJECT_ROOT / "scripts" / "test_arb.py"
    cmd = [sys.executable, str(script_path)]
    if command:
        cmd.append(command)

    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    sys.exit(result.returncode)


@click.group()
def test():
    """Run tests for the trading system."""
    pass


@test.command("unit")
@click.option("-k", "--keyword", help="Only run tests matching the keyword")
@click.option("-x", "--exitfirst", is_flag=True, help="Stop on first failure")
def test_unit(keyword, exitfirst):
    """Run all pytest unit tests."""
    extra_args = []
    if keyword:
        extra_args.extend(["-k", keyword])
    if exitfirst:
        extra_args.append("-x")

    run_pytest("tests/", extra_args)


@test.command("arb")
def test_arb():
    """Run all arb algorithm tests."""
    run_test_arb("all")


@test.command("arb-detect")
def test_arb_detect():
    """Run arb opportunity detection tests."""
    run_test_arb("detect")


@test.command("arb-execute")
def test_arb_execute():
    """Run arb full execution tests."""
    run_test_arb("execute")


@test.command("arb-failure")
def test_arb_failure():
    """Run arb failure handling and rollback tests."""
    run_test_arb("failure")


@test.command("arb-live")
def test_arb_live():
    """Run arb tests with live market data (read-only)."""
    run_test_arb("live")


@test.command("arb-capital")
def test_arb_capital():
    """Run capital management tests."""
    run_test_arb("capital")


@test.command("integration")
def test_integration():
    """Run integration E2E tests."""
    run_pytest("tests/test_arb_integration_e2e.py", [])


@test.command("spread")
def test_spread():
    """Run spread detector tests."""
    run_pytest("tests/test_spread_detector.py", [])


@test.command("all")
@click.option("--skip-live", is_flag=True, help="Skip live market data tests")
def test_all(skip_live):
    """Run all tests (unit + arb + integration + spread)."""
    click.echo("=" * 60)
    click.echo("  Running All Tests")
    click.echo("=" * 60)

    # Track results
    results = []

    # 1. Unit tests
    click.echo("\n[1/4] Running unit tests...")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
        cwd=PROJECT_ROOT
    )
    results.append(("Unit tests", result.returncode == 0))

    # 2. Arb algorithm tests
    click.echo("\n[2/4] Running arb algorithm tests...")
    script_path = PROJECT_ROOT / "scripts" / "test_arb.py"
    result = subprocess.run(
        [sys.executable, str(script_path), "all"],
        cwd=PROJECT_ROOT
    )
    results.append(("Arb tests", result.returncode == 0))

    # 3. Integration tests
    click.echo("\n[3/4] Running integration E2E tests...")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_arb_integration_e2e.py", "-v", "--tb=short"],
        cwd=PROJECT_ROOT
    )
    results.append(("Integration tests", result.returncode == 0))

    # 4. Live tests (optional)
    if not skip_live:
        click.echo("\n[4/4] Running live market tests...")
        result = subprocess.run(
            [sys.executable, str(script_path), "live"],
            cwd=PROJECT_ROOT
        )
        results.append(("Live tests", result.returncode == 0))
    else:
        click.echo("\n[4/4] Skipping live market tests (--skip-live)")
        results.append(("Live tests", None))

    # Summary
    click.echo("\n" + "=" * 60)
    click.echo("  Test Summary")
    click.echo("=" * 60)

    passed = 0
    failed = 0
    skipped = 0

    for name, success in results:
        if success is None:
            status = "SKIP"
            skipped += 1
        elif success:
            status = "PASS"
            passed += 1
        else:
            status = "FAIL"
            failed += 1
        click.echo(f"  [{status}] {name}")

    click.echo(f"\nResult: {passed} passed, {failed} failed, {skipped} skipped")

    # Exit with failure if any test failed
    if failed > 0:
        sys.exit(1)
