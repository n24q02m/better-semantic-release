"""Entrypoint for the `semantic-release` module."""
# ruff: noqa: T201, print statements are fine here as this is for cli entry only

from __future__ import annotations

import sys

import rich.markup
from rich.console import Console

from semantic_release import globals
from semantic_release.cli.commands.main import main as cli_main
from semantic_release.enums import SemanticReleaseLogLevels

err_console = Console(stderr=True)


def main() -> None:
    try:
        cli_main(args=sys.argv[1:])
        err_console.print(
            "[bold green]semantic-release completed successfully.[/bold green]"
        )
    except KeyboardInterrupt:
        err_console.print("\n[bold orange1]-- User Abort! --[/bold orange1]")
        sys.exit(127)
    except Exception as err:  # noqa: BLE001, graceful error handling across application
        if globals.log_level <= SemanticReleaseLogLevels.DEBUG:
            err_console.print_exception(show_locals=False)

        error_lines = [
            f"::ERROR:: {rich.markup.escape(line)}" for line in str(err).splitlines()
        ]
        err_console.print(str.join("\n", error_lines))

        if globals.log_level > SemanticReleaseLogLevels.DEBUG:
            err_console.print(
                "[orange1]Run semantic-release in very verbose mode (-vv) to see the full traceback.[/orange1]"
            )

        sys.exit(1)


if __name__ == "__main__":
    main()
