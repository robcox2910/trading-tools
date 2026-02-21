"""CLI entry point for the backtester.

Provide backward-compatible access to the Typer app and main entry
point. All command logic lives in the cli subpackage.
"""

from trading_tools.apps.backtester.cli import app

__all__ = ["app", "main"]


def main() -> None:
    """Run the backtester CLI application."""
    app()


if __name__ == "__main__":
    main()
