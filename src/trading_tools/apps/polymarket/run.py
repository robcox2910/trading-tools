"""CLI entry point for the Polymarket prediction market app.

Provide backward-compatible access to the Typer app and main entry
point.  All command logic lives in the cli subpackage.
"""

from trading_tools.apps.polymarket.cli import app

__all__ = ["app", "main"]


def main() -> None:
    """Run the Polymarket CLI application."""
    app()


if __name__ == "__main__":
    main()
