"""Architectural invariants for MrClean.

These tests enforce the structural rules of the codebase:
- Every strategy implements I_Strategy or DepthStrategyBase
- Every strategy is registered in main.py
- Every config follows the StrategyConfig pattern
- No magic strings for sides/actions
- No orphaned or copy-pasted config classes

Run with: python3 -m pytest tests/test_architecture.py -v
"""

import importlib
import inspect
import pkgutil
import re
from pathlib import Path
from typing import List, Set, Tuple, Type

import pytest

# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------

STRATEGIES_DIR = Path(__file__).parent.parent / "strategies"


def _all_strategy_modules():
    """Import all modules under strategies/."""
    import strategies

    results = []
    for importer, modname, ispkg in pkgutil.walk_packages(
        strategies.__path__, prefix="strategies."
    ):
        try:
            mod = importlib.import_module(modname)
            results.append(mod)
        except ImportError:
            pass
    return results


def _all_strategy_classes() -> List[Tuple[str, Type]]:
    """Find every concrete class that subclasses I_Strategy."""
    from strategies.i_strategy import I_Strategy

    results = []
    for mod in _all_strategy_modules():
        for name, obj in inspect.getmembers(mod, inspect.isclass):
            if (
                issubclass(obj, I_Strategy)
                and obj is not I_Strategy
                and not inspect.isabstract(obj)
                and not name.startswith("_")
            ):
                results.append((name, obj))
    # Deduplicate (same class may appear in multiple modules via re-export)
    seen: Set[Type] = set()
    unique = []
    for name, cls in results:
        if cls not in seen:
            seen.add(cls)
            unique.append((name, cls))
    return unique


def _all_depth_strategy_classes() -> List[Tuple[str, Type]]:
    """Find every concrete class that subclasses DepthStrategyBase."""
    try:
        from strategies.depth_strategy_base import DepthStrategyBase
    except ImportError:
        return []

    results = []
    for mod in _all_strategy_modules():
        for name, obj in inspect.getmembers(mod, inspect.isclass):
            if (
                issubclass(obj, DepthStrategyBase)
                and obj is not DepthStrategyBase
                and not inspect.isabstract(obj)
                and not name.startswith("_")
            ):
                results.append((name, obj))
    seen: Set[Type] = set()
    unique = []
    for name, cls in results:
        if cls not in seen:
            seen.add(cls)
            unique.append((name, cls))
    return unique


def _all_config_classes() -> List[Tuple[str, Type]]:
    """Find every concrete subclass of StrategyConfig."""
    from strategies.strategy_types import StrategyConfig

    results = []
    for mod in _all_strategy_modules():
        for name, obj in inspect.getmembers(mod, inspect.isclass):
            if (
                issubclass(obj, StrategyConfig)
                and obj is not StrategyConfig
                and not name.startswith("_")
            ):
                results.append((name, obj))
    seen: Set[Type] = set()
    unique = []
    for name, cls in results:
        if cls not in seen:
            seen.add(cls)
            unique.append((name, cls))
    return unique


def _get_registry():
    """Get the strategy registry from main.py."""
    from main import _register_builtins, _STRATEGY_REGISTRY

    _register_builtins()
    return dict(_STRATEGY_REGISTRY)


# ---------------------------------------------------------------------------
# I_Strategy interface compliance
# ---------------------------------------------------------------------------


class TestStrategyInterface:
    """Every I_Strategy subclass must fully implement the interface."""

    def test_all_strategies_implement_interface(self):
        """No strategy should leave abstract methods unimplemented."""
        for name, cls in _all_strategy_classes():
            leftover = getattr(cls, "__abstractmethods__", set())
            assert not leftover, (
                f"{name} has unimplemented abstract methods: {leftover}"
            )

    def test_get_signal_has_return_annotation(self):
        """get_signal must have a return type annotation."""
        for name, cls in _all_strategy_classes():
            hints = {}
            # Walk MRO to find the concrete get_signal
            for klass in cls.__mro__:
                if "get_signal" in klass.__dict__:
                    try:
                        hints = inspect.get_annotations(klass.get_signal)
                    except Exception:
                        hints = getattr(klass.get_signal, "__annotations__", {})
                    break
            ret = hints.get("return")
            assert ret is not None, (
                f"{name}.get_signal() is missing a return type annotation"
            )

    def test_strategies_have_stop_method(self):
        """Every strategy must implement stop() for graceful shutdown."""
        for name, cls in _all_strategy_classes():
            assert hasattr(cls, "stop"), f"{name} is missing stop() method"
            # Make sure it's not still abstract
            assert "stop" not in getattr(cls, "__abstractmethods__", set()), (
                f"{name}.stop() is still abstract"
            )


# ---------------------------------------------------------------------------
# Strategy registration
# ---------------------------------------------------------------------------


class TestStrategyRegistration:
    """Every strategy should be registered and discoverable via main.py."""

    def test_all_istrategy_classes_registered(self):
        """Every I_Strategy subclass should appear in the registry."""
        registry = _get_registry()
        registered_classes = {info["cls"] for info in registry.values()}

        for name, cls in _all_strategy_classes():
            assert cls in registered_classes, (
                f"{name} implements I_Strategy but is not registered "
                f"in main.py _register_builtins()"
            )

    def test_all_depth_strategy_classes_registered(self):
        """Every DepthStrategyBase subclass should appear in the registry."""
        registry = _get_registry()
        registered_classes = {info["cls"] for info in registry.values()}

        for name, cls in _all_depth_strategy_classes():
            assert cls in registered_classes, (
                f"{name} extends DepthStrategyBase but is not registered "
                f"in main.py _register_builtins()"
            )

    def test_registry_entries_have_descriptions(self):
        """Every registered strategy must have a description."""
        registry = _get_registry()
        for reg_name, info in registry.items():
            desc = info.get("description", "")
            assert desc, f"Strategy '{reg_name}' is registered without a description"

    def test_no_duplicate_strategy_classes(self):
        """No class should be registered under multiple names."""
        registry = _get_registry()
        seen_classes = {}
        for reg_name, info in registry.items():
            cls = info["cls"]
            if cls in seen_classes:
                pytest.fail(
                    f"{cls.__name__} is registered as both "
                    f"'{seen_classes[cls]}' and '{reg_name}'"
                )
            seen_classes[cls] = reg_name


# ---------------------------------------------------------------------------
# Config pattern compliance
# ---------------------------------------------------------------------------


class TestStrategyConfigs:
    """Config classes must follow the StrategyConfig pattern."""

    def test_configs_have_from_yaml_dict(self):
        """Every StrategyConfig subclass must implement from_yaml_dict."""
        for name, cls in _all_config_classes():
            assert "from_yaml_dict" in cls.__dict__, (
                f"{name} is missing its own from_yaml_dict() — "
                f"it must override the base class method"
            )

    def test_configs_have_to_yaml_dict(self):
        """Every StrategyConfig subclass must implement to_yaml_dict."""
        for name, cls in _all_config_classes():
            assert "to_yaml_dict" in cls.__dict__, (
                f"{name} is missing its own to_yaml_dict() — "
                f"it must override the base class method"
            )

    def test_from_yaml_dict_returns_own_type(self):
        """from_yaml_dict return annotation should reference its own class."""
        for name, cls in _all_config_classes():
            method = cls.__dict__.get("from_yaml_dict")
            if method is None:
                continue
            # Unwrap classmethod/staticmethod descriptors to get the function
            func = method
            if isinstance(func, (classmethod, staticmethod)):
                func = func.__func__
            hints = getattr(func, "__annotations__", {})
            ret = hints.get("return", "")
            if not ret:
                continue  # No annotation at all — caught by mypy instead
            # The return annotation should mention the class name (as string or type)
            if isinstance(ret, str):
                assert name in ret or cls.__name__ in ret, (
                    f"{name}.from_yaml_dict() return annotation is '{ret}' — "
                    f"should return '{name}' (possible copy-paste error)"
                )

    def test_registered_configs_have_yaml_files(self):
        """If a registered strategy specifies a yaml_path, the file must exist."""
        registry = _get_registry()
        for reg_name, info in registry.items():
            yaml_path = info.get("yaml_path")
            if yaml_path is None:
                continue
            assert Path(yaml_path).exists(), (
                f"Strategy '{reg_name}' references config {yaml_path} "
                f"but the file doesn't exist"
            )

    def test_configs_are_dataclasses(self):
        """All config classes should be dataclasses."""
        import dataclasses

        for name, cls in _all_config_classes():
            assert dataclasses.is_dataclass(cls), f"{name} should be a @dataclass"


# ---------------------------------------------------------------------------
# Code quality: no magic strings, no copy-paste
# ---------------------------------------------------------------------------


class TestCodeQuality:
    """Catch common code quality issues in strategy files."""

    def _strategy_source_files(self) -> List[Path]:
        """Get all *_strategy.py files."""
        return list(STRATEGIES_DIR.glob("*_strategy.py"))

    def test_no_raw_side_strings_in_order_calls(self):
        """Strategies should use Side enum, not raw 'yes'/'no' strings."""
        violations = []
        # Match side = "yes"/"no" but not position_side or other prefixed vars
        # Also match side="yes" in function call kwargs
        pattern = re.compile(
            r"""(?<![a-zA-Z_])side\s*=\s*['"](?:yes|no)['"]""", re.IGNORECASE
        )
        for path in self._strategy_source_files():
            content = path.read_text()
            matches = pattern.findall(content)
            if matches:
                violations.append(f"{path.name}: {matches}")

        assert not violations, (
            "Use Side.YES / Side.NO instead of raw strings:\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_no_raw_action_strings_in_order_calls(self):
        """Strategies should use Action enum, not raw 'buy'/'sell' strings."""
        violations = []
        pattern = re.compile(r"""action\s*=\s*['"](?:buy|sell)['"]""", re.IGNORECASE)
        for path in self._strategy_source_files():
            content = path.read_text()
            matches = pattern.findall(content)
            if matches:
                violations.append(f"{path.name}: {matches}")

        assert not violations, (
            "Use Action.BUY / Action.SELL instead of raw strings:\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_strategies_use_signal_factory_methods(self):
        """Strategies should use Signal.no_signal() / Signal.buy(), not raw constructors."""
        violations = []
        # Match standalone Signal( but not Signal.no_signal( or Signal.buy(
        # Negative lookbehind excludes domain-specific signals like BlowoutSignal(
        pattern = re.compile(r"(?<![A-Za-z_])Signal\(\s*(?!no_signal|buy)")
        # Pattern to check if Signal is imported from strategy_types (the new type)
        imports_new_signal = re.compile(
            r"from\s+\.?strategies\.strategy_types\s+import\s+.*\bSignal\b"
        )
        for path in self._strategy_source_files():
            content = path.read_text()
            # Only flag if they import the new Signal type from strategy_types
            if not imports_new_signal.search(content):
                continue
            matches = pattern.findall(content)
            if matches:
                violations.append(path.name)

        assert not violations, (
            f"Use Signal.no_signal() / Signal.buy() factory methods "
            f"instead of raw Signal() constructor in: {violations}"
        )
