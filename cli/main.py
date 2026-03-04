"""Unified Trading CLI - Main entry point.

Usage:
    python -m cli test unit          # Run all pytest unit tests
    python -m cli test arb           # Run all arb algorithm tests
    python -m cli test arb-detect    # Detection tests only
"""

import click

from .commands.test import test


@click.group()
@click.version_option(version="1.0.0", prog_name="trading")
def cli():
    """Unified Trading CLI - Test runner."""
    pass


# Register command groups
cli.add_command(test)


if __name__ == "__main__":
    cli()
