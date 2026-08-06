from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import click
import rich.markup
from click.core import ParameterSource
from git import InvalidGitRepositoryError
from pydantic import ValidationError

# BSR-PATCH: actionable error messages (better-semantic-release)
from semantic_release.bsr.config import load_bsr_config
from semantic_release.bsr.messages import format_actionable
from semantic_release.cli.config import (
    RawConfig,
    RuntimeContext,
)
from semantic_release.cli.util import load_raw_config_file, rprint
from semantic_release.errors import (
    DetachedHeadGitError,
    InvalidConfiguration,
    MissingGitRemote,
    NotAReleaseBranch,
    ParserLoadError,
)

if TYPE_CHECKING:  # pragma: no cover
    from semantic_release.cli.config import GlobalCommandLineOptions

    class CliContext(click.Context):
        obj: CliContextObj


class CliContextObj:
    def __init__(
        self,
        ctx: click.Context,
        logger: logging.Logger,
        global_opts: GlobalCommandLineOptions,
    ) -> None:
        self.ctx = ctx
        self.logger = logger
        self.global_opts = global_opts
        self._raw_config: RawConfig | None = None
        self._runtime_ctx: RuntimeContext | None = None

    @property
    def raw_config(self) -> RawConfig:
        if self._raw_config is None:
            self._raw_config = self._init_raw_config()
        return self._raw_config

    @property
    def runtime_ctx(self) -> RuntimeContext:
        """
        Lazy load the runtime context. This is done to avoid configuration loading when
        the command is not run. This is useful for commands like `--help` and `--version`
        """
        if self._runtime_ctx is None:
            self._runtime_ctx = self._init_runtime_ctx()
        return self._runtime_ctx

    def _init_raw_config(self) -> RawConfig:
        config_path = Path(self.global_opts.config_file)
        conf_file_exists = config_path.exists()
        was_conf_file_user_provided = bool(
            self.ctx.get_parameter_source("config_file")
            not in (
                ParameterSource.DEFAULT,
                ParameterSource.DEFAULT_MAP,
            )
        )

        # TODO: Evaluate Exception catches
        try:
            if was_conf_file_user_provided and not conf_file_exists:
                raise FileNotFoundError(  # noqa: TRY301
                    f"File {self.global_opts.config_file} does not exist"
                )

            config_obj = (
                {} if not conf_file_exists else load_raw_config_file(config_path)
            )
            if not config_obj:
                self.logger.info(
                    "configuration empty, falling back to default configuration"
                )

            return RawConfig.model_validate(config_obj)
        except FileNotFoundError as exc:
            rprint(f"[bold red][:x:] {rich.markup.escape(str(exc))}[/bold red]")
            self.ctx.exit(2)
        except (
            ValidationError,
            InvalidConfiguration,
            InvalidGitRepositoryError,
        ) as exc:
            # BSR-PATCH: actionable error messages (better-semantic-release)
            bsr_cfg = load_bsr_config(self.global_opts.config_file)
            msg = format_actionable(exc) if bsr_cfg.actionable_errors else None
            rprint(f"[bold red][:x:] {rich.markup.escape(msg or str(exc))}[/bold red]")
            self.ctx.exit(1)

    def _init_runtime_ctx(self) -> RuntimeContext:
        # TODO: Evaluate Exception catches
        try:
            runtime = RuntimeContext.from_raw_config(
                self.raw_config,
                global_cli_options=self.global_opts,
            )
        except NotAReleaseBranch as exc:
            rprint(
                f"[bold {'red' if self.global_opts.strict else 'orange1'}]{rich.markup.escape(str(exc))}"
            )
            # If not strict, exit 0 so other processes can continue. For example, in
            # multibranch CI it might be desirable to run a non-release branch's pipeline
            # without specifying conditional execution of PSR based on branch name
            self.ctx.exit(2 if self.global_opts.strict else 0)
        except (
            DetachedHeadGitError,
            InvalidConfiguration,
            InvalidGitRepositoryError,
            # BSR-PATCH: actionable error messages (better-semantic-release).
            # MissingGitRemote and ParserLoadError are newly caught here -- stock
            # PSR (this except tuple, pre-BSR) leaves them UNCAUGHT (raw traceback).
            # When the flag is off, re-raise them unchanged to preserve that
            # byte-for-byte; the four pre-existing types keep their original
            # click.echo(str(exc)) handling either way.
            MissingGitRemote,
            ParserLoadError,
            ValidationError,
        ) as exc:
            bsr_cfg = load_bsr_config(self.global_opts.config_file)
            if not bsr_cfg.actionable_errors and isinstance(
                exc, (MissingGitRemote, ParserLoadError)
            ):
                raise
            msg = format_actionable(exc) if bsr_cfg.actionable_errors else None
            rprint(f"[bold red][:x:] {rich.markup.escape(msg or str(exc))}[/bold red]")
            self.ctx.exit(1)

        # This allows us to mask secrets in the logging
        # by applying it to all the configured handlers
        for handler in logging.getLogger().handlers:
            handler.addFilter(runtime.masker)

        return runtime
