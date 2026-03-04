"""CLI commands for feature management system."""

import argparse
from pathlib import Path
from typing import Optional

from core.feature_manager import Feature, FeatureManager


def cmd_features_status(args: argparse.Namespace) -> int:
    """Show status of all features."""
    fm = FeatureManager(Path.cwd())
    statuses = fm.get_all_statuses()

    print("\n" + "=" * 80)
    print("FEATURE STATUS")
    print("=" * 80)

    for feature, status in statuses.items():
        enabled_str = "✓ ENABLED" if status.enabled else "✗ DISABLED"
        color = "\033[0;32m" if status.enabled else "\033[0;31m"
        reset = "\033[0m"

        print(f"\n{color}{enabled_str}{reset} {feature.value}")
        print(f"  Scope: {status.scope}")
        print(f"  Description: {status.description}")
        print(f"  Applies to: {', '.join(status.applicable_strategies)}")
        print(f"  Config: {status.config_file} ({status.config_key})")

    print("\n" + "=" * 80)

    # Summary
    enabled_count = sum(1 for s in statuses.values() if s.enabled)
    print(f"\n{enabled_count}/{len(statuses)} features enabled")

    # Recommendations
    all_disabled = all(not s.enabled for s in statuses.values())
    if all_disabled:
        print("\n💡 Recommendation: Start with 'python main.py features enable empirical-kelly'")

    return 0


def cmd_features_enable(args: argparse.Namespace) -> int:
    """Enable a feature."""
    fm = FeatureManager(Path.cwd())
    feature_name = args.feature

    try:
        feature = Feature(feature_name)
    except ValueError:
        print(f"Error: Unknown feature '{feature_name}'")
        print(f"Available: {', '.join(f.value for f in Feature)}")
        return 1

    # Get current status
    status = fm.get_status(feature)

    if status.enabled:
        print(f"✓ {feature.value} is already enabled")
        return 0

    # Show what will be done
    print(f"\nEnabling: {feature.value}")
    print(f"  Scope: {status.scope}")
    print(f"  Applies to: {', '.join(status.applicable_strategies)}")
    print(f"  Config file: {status.config_file}")
    print()

    # Enable
    dry_run = getattr(args, 'dry_run', False)
    success = fm.enable(feature, dry_run=dry_run)

    if success:
        if dry_run:
            print("[DRY RUN] Feature would be enabled")
        else:
            print(f"✓ {feature.value} enabled successfully")

            # Validate
            validation = fm.validate(feature)
            if validation.get("valid"):
                print(f"✓ Configuration valid")
                # Show any additional info
                for key, val in validation.items():
                    if key != "valid":
                        print(f"  {key}: {val}")
            else:
                print(f"⚠️  Configuration issues:")
                for issue in validation.get("issues", [validation.get("reason")]):
                    print(f"  - {issue}")

        print(f"\nNext steps:")
        _print_next_steps(feature)

        return 0
    else:
        print(f"✗ Failed to enable {feature.value}")
        return 1


def cmd_features_disable(args: argparse.Namespace) -> int:
    """Disable a feature."""
    fm = FeatureManager(Path.cwd())
    feature_name = args.feature

    try:
        feature = Feature(feature_name)
    except ValueError:
        print(f"Error: Unknown feature '{feature_name}'")
        print(f"Available: {', '.join(f.value for f in Feature)}")
        return 1

    # Get current status
    status = fm.get_status(feature)

    if not status.enabled:
        print(f"✓ {feature.value} is already disabled")
        return 0

    # Confirm
    if not getattr(args, 'yes', False):
        print(f"\nDisabling: {feature.value}")
        print(f"  Scope: {status.scope}")
        response = input("\nAre you sure? (y/N): ")
        if response.lower() != 'y':
            print("Cancelled")
            return 0

    # Disable
    dry_run = getattr(args, 'dry_run', False)
    success = fm.disable(feature, dry_run=dry_run)

    if success:
        if dry_run:
            print("[DRY RUN] Feature would be disabled")
        else:
            print(f"✓ {feature.value} disabled successfully")
        return 0
    else:
        print(f"✗ Failed to disable {feature.value}")
        return 1


def cmd_features_validate(args: argparse.Namespace) -> int:
    """Validate all enabled features."""
    fm = FeatureManager(Path.cwd())
    statuses = fm.get_all_statuses()

    print("\n" + "=" * 80)
    print("FEATURE VALIDATION")
    print("=" * 80)

    all_valid = True

    for feature, status in statuses.items():
        if not status.enabled:
            continue

        print(f"\n{feature.value}:")
        validation = fm.validate(feature)

        if validation.get("valid"):
            print("  ✓ Valid")
            for key, val in validation.items():
                if key != "valid":
                    print(f"    {key}: {val}")
        else:
            print("  ✗ Invalid")
            all_valid = False
            issues = validation.get("issues", [validation.get("reason")])
            for issue in issues:
                print(f"    - {issue}")

    print("\n" + "=" * 80)

    if all_valid:
        print("\n✓ All enabled features are valid")
        return 0
    else:
        print("\n✗ Some features have configuration issues")
        return 1


def cmd_features_quickstart(args: argparse.Namespace) -> int:
    """Interactive quickstart wizard."""
    fm = FeatureManager(Path.cwd())

    print("\n" + "=" * 80)
    print("FEATURE QUICKSTART WIZARD")
    print("=" * 80)
    print("\nThis wizard will help you enable institutional quant features.")
    print()

    # Check current status
    statuses = fm.get_all_statuses()
    enabled_features = [f for f, s in statuses.items() if s.enabled]

    if enabled_features:
        print(f"Currently enabled: {', '.join(f.value for f in enabled_features)}")
        print()

    # Recommend starting point
    if not any(s.enabled for s in statuses.values()):
        print("💡 Recommended starting point: Empirical Kelly")
        print("   - Lowest risk (only affects position sizing)")
        print("   - Highest ROI (helps ALL strategies)")
        print("   - Easy to validate (clear metric: drawdown reduction)")
        print()

        response = input("Enable Empirical Kelly now? (Y/n): ")
        if response.lower() != 'n':
            fm.enable(Feature.EMPIRICAL_KELLY)
            print("\n✓ Empirical Kelly enabled!")
            print("\nNext steps:")
            _print_next_steps(Feature.EMPIRICAL_KELLY)
            return 0

    # Show menu
    print("\nAvailable features:")
    print()

    for i, (feature, status) in enumerate(statuses.items(), 1):
        enabled_str = "ENABLED" if status.enabled else "disabled"
        print(f"{i}. {feature.value} ({enabled_str})")
        print(f"   {status.description}")
        print(f"   Scope: {status.scope}")
        print()

    print("0. Exit")
    print()

    try:
        choice = int(input("Select feature to enable/disable (0-4): "))
        if choice == 0:
            return 0

        if choice < 1 or choice > len(statuses):
            print("Invalid choice")
            return 1

        feature = list(statuses.keys())[choice - 1]
        status = statuses[feature]

        if status.enabled:
            # Disable
            response = input(f"\nDisable {feature.value}? (y/N): ")
            if response.lower() == 'y':
                fm.disable(feature)
                print(f"✓ {feature.value} disabled")
        else:
            # Enable
            response = input(f"\nEnable {feature.value}? (Y/n): ")
            if response.lower() != 'n':
                fm.enable(feature)
                print(f"✓ {feature.value} enabled")
                print("\nNext steps:")
                _print_next_steps(feature)

    except (ValueError, EOFError, KeyboardInterrupt):
        print("\nCancelled")
        return 0

    return 0


def _print_next_steps(feature: Feature) -> None:
    """Print next steps after enabling a feature."""
    if feature == Feature.EMPIRICAL_KELLY:
        print("  1. Run portfolio rebalance:")
        print("     python main.py portfolio rebalance")
        print()
        print("  2. Check the logs for CV adjustments:")
        print("     grep 'empirical Kelly' logs/portfolio.log")
        print()
        print("  3. View current allocations:")
        print("     python main.py portfolio status")
        print()
        print("  Monitor for 1-2 weeks, then consider adding:")
        print("  - sequence-gap-detection (if using WebSocket feeds)")
        print("  - vpin-kill-switch (if running prediction MM)")
        print("  - as-reservation-price (if running prediction MM)")

    elif feature == Feature.VPIN_KILL_SWITCH:
        print("  1. Run prediction MM strategy:")
        print("     python main.py run prediction-mm")
        print()
        print("  2. Monitor for VPIN activations:")
        print("     grep 'VPIN KILL SWITCH' logs/prediction_mm.log")
        print()
        print("  Expect: 1-3 activations per week")
        print("  If > 5 per day: increase toxic_threshold to 0.80")

    elif feature == Feature.SEQUENCE_GAP_DETECTION:
        print("  1. Run a strategy using WebSocket feeds:")
        print("     python main.py run crypto-scalp")
        print("     python main.py run prediction-mm")
        print()
        print("  2. Monitor for sequence gaps:")
        print("     grep 'sequence gap' logs/*.log")
        print()
        print("  Note: Kalshi doesn't provide seq numbers yet.")
        print("  Only Coinbase supports this currently.")

    elif feature == Feature.AS_RESERVATION_PRICE:
        print("  1. Run prediction MM strategy:")
        print("     python main.py run prediction-mm")
        print()
        print("  2. Monitor inventory levels:")
        print("     python main.py portfolio status")
        print()
        print("  Expect: Inventory trends toward 0")
        print("  If still extreme after 3 days: increase risk_aversion to 0.07-0.10")
