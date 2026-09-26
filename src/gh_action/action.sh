#!/bin/bash

set -e

explicit_run_cmd() {
  local cmd_str=""
  for arg in "$@"; do
    cmd_str="$cmd_str \"$arg\""
  done
  printf '%s\n' "$> ${cmd_str# }"
  "$@"
}

# Same as explicit_run_cmd, but logs to STDERR: use it when STDOUT is
# redirected to a data file (plan mode captures the JSON document on stdout).
explicit_run_cmd_to_stderr() {
  local cmd_str=""
  for arg in "$@"; do
    cmd_str="$cmd_str \"$arg\""
  done
  printf '%s\n' "$> ${cmd_str# }" >&2
  "$@"
}

# Convert "true"/"false" into command line args, returns "" if not defined
append_boolean_action_input() {
	local array_name="$1"
	local -r input_name="$2"
	local -r flag_value="$3"
	local -r if_true="$4"
	local -r if_false="$5"

	if [ "$flag_value" = "true" ]; then
		if [ -n "$if_true" ]; then eval "$array_name+=(\"\$if_true\")"; fi
	elif [ "$flag_value" = "false" ]; then
		if [ -n "$if_false" ]; then eval "$array_name+=(\"\$if_false\")"; fi
	elif [ -n "$flag_value" ]; then
		printf 'Error: Invalid value for input %s: %s is not "true" or "false"\n' \
			"$input_name" "$flag_value" >&2
		return 1
	fi
}

# Convert inputs to command line arguments
ROOT_OPTIONS=()

# Select the execution surface: "version" (default, legacy behavior),
# "plan" (read-only release plan) or "verify" (fail-closed safety gate).
MODE="${INPUT_MODE:-version}"
case "$MODE" in
version | plan | verify) ;;
*)
	printf "Error: Input 'mode' must be one of: version, plan, verify\n" >&2
	exit 1
	;;
esac

# Write one GitHub Actions step output (no-op outside Actions where
# GITHUB_OUTPUT is unset).
write_step_output() {
	if [ -n "${GITHUB_OUTPUT:-}" ]; then
		printf '%s=%s\n' "$1" "$2" >>"$GITHUB_OUTPUT"
	fi
}

if ! printf '%s\n' "$INPUT_VERBOSITY" | grep -qE '^[0-9]+$'; then
	printf "Error: Input 'verbosity' must be a positive integer\n" >&2
	exit 1
fi

VERBOSITY_OPTIONS=""
for ((i = 0; i < INPUT_VERBOSITY; i++)); do
	[ "$i" -eq 0 ] && VERBOSITY_OPTIONS="-"
	VERBOSITY_OPTIONS+="v"
done

if [ -n "$VERBOSITY_OPTIONS" ]; then
	ROOT_OPTIONS+=("$VERBOSITY_OPTIONS")
fi

if [ -n "$INPUT_CONFIG_FILE" ]; then
	# Check if the file exists
	if [ ! -f "$INPUT_CONFIG_FILE" ]; then
		printf "Error: Input 'config_file' does not exist: %s\n" "$INPUT_CONFIG_FILE" >&2
		exit 1
	fi

	ROOT_OPTIONS+=("--config" "$INPUT_CONFIG_FILE")
fi

append_boolean_action_input ROOT_OPTIONS "strict" "$INPUT_STRICT" "--strict" "" || exit 1
append_boolean_action_input ROOT_OPTIONS "no_operation_mode" "$INPUT_NO_OPERATION_MODE" "--noop" "" || exit 1

ARGS=()
# v10 Breaking change as prerelease should be as_prerelease to match
append_boolean_action_input ARGS "prerelease" "$INPUT_PRERELEASE" "--as-prerelease" "" || exit 1
append_boolean_action_input ARGS "commit" "$INPUT_COMMIT" "--commit" "--no-commit" || exit 1
append_boolean_action_input ARGS "tag" "$INPUT_TAG" "--tag" "--no-tag" || exit 1
append_boolean_action_input ARGS "push" "$INPUT_PUSH" "--push" "--no-push" || exit 1
append_boolean_action_input ARGS "changelog" "$INPUT_CHANGELOG" "--changelog" "--no-changelog" || exit 1
append_boolean_action_input ARGS "vcs_release" "$INPUT_VCS_RELEASE" "--vcs-release" "--no-vcs-release" || exit 1
append_boolean_action_input ARGS "build" "$INPUT_BUILD" "" "--skip-build" || exit 1

# Handle --patch, --minor, --major
# https://stackoverflow.com/a/47541882
valid_force_levels=("prerelease" "patch" "minor" "major")
if [ -z "$INPUT_FORCE" ]; then
	true # do nothing if 'force' input is not set
elif printf '%s\0' "${valid_force_levels[@]}" | grep -Fxzq "$INPUT_FORCE"; then
	ARGS+=("--$INPUT_FORCE")
else
	printf "Error: Input 'force' must be one of: %s\n" "${valid_force_levels[@]}" >&2
fi

if [ -n "$INPUT_BUILD_METADATA" ]; then
	ARGS+=("--build-metadata" "$INPUT_BUILD_METADATA")
fi

if [ -n "$INPUT_PRERELEASE_TOKEN" ]; then
	ARGS+=("--prerelease-token" "$INPUT_PRERELEASE_TOKEN")
fi

# Change to configured directory
cd "${INPUT_DIRECTORY}"

# Set Git details
if ! [ "${INPUT_GIT_COMMITTER_NAME:="-"}" = "-" ]; then
	git config --global user.name "$INPUT_GIT_COMMITTER_NAME"
fi
if ! [ "${INPUT_GIT_COMMITTER_EMAIL:="-"}" = "-" ]; then
	git config --global user.email "$INPUT_GIT_COMMITTER_EMAIL"
fi
if [ "${INPUT_GIT_COMMITTER_NAME:="-"}" != "-" ] && [ "${INPUT_GIT_COMMITTER_EMAIL:="-"}" != "-" ]; then
	# Must export this value to the environment for PSR to consume the override
	export GIT_COMMIT_AUTHOR="$INPUT_GIT_COMMITTER_NAME <$INPUT_GIT_COMMITTER_EMAIL>"
fi

if [[ -n "$INPUT_SSH_PUBLIC_SIGNING_KEY" && -n "$INPUT_SSH_PRIVATE_SIGNING_KEY" ]]; then
	echo "SSH Key pair found, configuring signing..."

	# Create the private key with restrictive permissions from the first byte.
	mkdir -m 700 -p ~/.ssh
	chmod 700 ~/.ssh
	printf '%b\n' "$INPUT_SSH_PUBLIC_SIGNING_KEY" >>~/.ssh/signing_key.pub
	(
		umask 077
		printf '%b\n' "$INPUT_SSH_PRIVATE_SIGNING_KEY" >~/.ssh/signing_key
	)

	# Enable ssh-agent & add signing key
	eval "$(ssh-agent -s)"
	ssh-add ~/.ssh/signing_key

	# Create allowed_signers file for git
	if [ "${INPUT_GIT_COMMITTER_EMAIL:="-"}" = "-" ]; then
		echo >&2 "git_committer_email must be set to use SSH key signing!"
		exit 1
	fi
	(
		umask 077
		printf '%s %b\n' "$INPUT_GIT_COMMITTER_EMAIL" "$INPUT_SSH_PUBLIC_SIGNING_KEY" >~/.ssh/allowed_signers
	)

	# Configure git for signing
	git config --global gpg.format ssh
	git config --global gpg.ssh.allowedSignersFile ~/.ssh/allowed_signers
	git config --global user.signingKey ~/.ssh/signing_key
	git config --global commit.gpgsign true
	git config --global tag.gpgsign true
fi

# Copy inputs into correctly-named environment variables
export GH_TOKEN="${INPUT_GITHUB_TOKEN}"

if [ "$MODE" = "plan" ]; then
	# Read-only release plan: never mutates anything. The plan always runs with
	# its own --strict so a blocked plan surfaces through the exit code
	# (exit 1) while the JSON document is still emitted. The step itself only
	# fails when the user opted in via the strict input; otherwise the verdict
	# is exposed through the plan_blocked output.
	PLAN_JSON_FILE="${GITHUB_WORKSPACE:-$(pwd)}/release-plan.json"

	set +e
	explicit_run_cmd_to_stderr "$PSR_VENV_BIN/semantic-release" "${ROOT_OPTIONS[@]}" "plan" "--strict" "--format" "json" >"$PLAN_JSON_FILE"
	plan_rc=$?
	set -e

	if [ "$plan_rc" -ge 2 ]; then
		# Usage/crash exit codes: fail loudly without claiming a verdict.
		exit "$plan_rc"
	fi

	plan_blocked=false
	if [ "$plan_rc" -eq 1 ]; then
		plan_blocked=true
	fi

	# Human-readable rendering for the job summary (Actions only; deterministic
	# offline, so both invocations see the same repository state).
	if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
		explicit_run_cmd_to_stderr "$PSR_VENV_BIN/semantic-release" "${ROOT_OPTIONS[@]}" "plan" "--format" "markdown" >>"$GITHUB_STEP_SUMMARY" || true
	fi

	write_step_output plan_blocked "$plan_blocked"
	write_step_output plan_json "$PLAN_JSON_FILE"

	if [ "$INPUT_STRICT" = "true" ] && [ "$plan_blocked" = "true" ]; then
		exit 1
	fi
	exit 0
fi

if [ "$MODE" = "verify" ]; then
	# Fail-closed safety gate: verify exits non-zero when a policy blocker
	# trips. Preserve that exit code while still reporting the verdict.
	set +e
	explicit_run_cmd "$PSR_VENV_BIN/semantic-release" "${ROOT_OPTIONS[@]}" "verify"
	verify_rc=$?
	set -e

	if [ "$verify_rc" -eq 0 ]; then
		write_step_output plan_blocked false
	else
		write_step_output plan_blocked true
	fi
	exit "$verify_rc"
fi

# Run Semantic Release (explicitly use the GitHub action version)
explicit_run_cmd "$PSR_VENV_BIN/semantic-release" "${ROOT_OPTIONS[@]}" "version" "${ARGS[@]}"
