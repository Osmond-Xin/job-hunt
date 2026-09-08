#!/usr/bin/env bash
# Run `codex exec` as an LLM provider for the pipeline's `local_command` tier.
#
# The provider pipes a prompt on stdin and reads the answer from stdout, so the
# only job here is to strip what `codex exec` prints around the answer: a
# banner, the workdir, the model line, the echoed prompt and a token count. The
# `-o/--output-last-message` flag writes just the final assistant message to a
# file, which is exactly the answer, so the wrapper emits that file and sends
# every other byte codex produces to stderr where it stays out of the artifact.
#
# Sandboxed read-only and with approvals off: these calls score a posting or
# rewrite a résumé from a prompt that is already complete. They have no reason
# to touch the filesystem, and a prompt that talks a coding agent into doing so
# is exactly the failure this guards against.
set -euo pipefail

last_message="$(mktemp -t codex-llm)"
trap 'rm -f "$last_message"' EXIT

# No --model by default. `--ignore-user-config` is what makes this reliable:
# without it codex reads the operator's own config, and the model named there
# 404s ("The model `gpt-5.5` does not exist or you do not have access to it").
# Pinning a model here reintroduces exactly that failure — an early version of
# this wrapper defaulted to the name off the session banner and every call in a
# nine-job batch died on it. Let the CLI pick its own default; set
# JOB_HUNT_CODEX_MODEL only to override deliberately, and check it works first.
codex exec \
    ${JOB_HUNT_CODEX_MODEL:+--model "$JOB_HUNT_CODEX_MODEL"} \
    --sandbox read-only \
    --skip-git-repo-check \
    --ephemeral \
    --ignore-user-config \
    --output-last-message "$last_message" \
    - >&2

# `codex exec` exits 0 with an empty last message when the model returns
# nothing. Failing here beats handing an empty string back to a node that will
# write it into a résumé.
if [ ! -s "$last_message" ]; then
    echo "codex exec produced no final message" >&2
    exit 1
fi

cat "$last_message"
