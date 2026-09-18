.. _troubleshooting:

Troubleshooting
===============

- Check your configuration file for :ref:`configuration`
- Check your Git tags match your :ref:`tag_format <config-tag_format>`; tags using
  other formats are ignored during calculation of the next version.

.. _troubleshooting-verbosity:

Increasing Verbosity
--------------------

If you are having trouble with Python Semantic Release or would like to see additional
information about the actions that it is taking, you can use the top-level
:ref:`cmd-main-option-verbosity` option. This can be supplied multiple times to increase
the logging verbosity of the :ref:`cmd-main` command or any of its subcommands during
their execution. You can supply this as many times as you like, but supplying more than
twice has no effect.

Supply :ref:`cmd-main-option-verbosity` once for ``INFO`` output, and twice for ``DEBUG``.

For example::

    semantic-release -vv version --print

.. note::
   The :ref:`cmd-main-option-verbosity` option must be supplied to the top-level
   ``semantic-release`` command, before the name of any sub-command.

.. warning::
   The volume of logs when using ``DEBUG`` verbosity may be significantly increased,
   compared to ``INFO`` or the default ``WARNING``, and as a result executing commands
   with ``semantic-release`` may be significantly slower than when using ``DEBUG``.

.. note::
   The provided GitHub action sets the verbosity level to INFO by default.

BSR notes command failed / produced no release notes
----------------------------------------------------

``bsr.notes.mode = "fragments"`` or ``"command"`` demand a manual notes source
for every release. Add a fragment to ``changes/``, fix the configured command,
switch to ``mode = "hybrid"`` (fragments become optional), or set
``allow_empty_command_output = true`` if an empty command output is acceptable.

bsr.notes command failed (exit N)
---------------------------------

The ``[tool.semantic_release.bsr.notes]`` command runs with the repository as
its working directory; its stderr is surfaced in the error. Run the command
manually with the same interpreter and environment the release job uses.

Unknown [tool.semantic_release.bsr.notes] fields
------------------------------------------------

Only ``mode``, ``fragments_dir``, ``command``, ``allow_empty_command_output``
and ``require_manual_notes`` are recognized. Anything else fails the run --
typos must not silently disable a release gate.
