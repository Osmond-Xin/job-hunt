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

model="${JOB_HUNT_CODEX_MODEL:-gpt-5.5}"
last_message="$(mktemp -t codex-llm)"
trap 'rm -f "$last_message"' EXIT

codex exec \
    --model "$model" \
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
