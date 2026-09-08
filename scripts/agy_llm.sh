#!/usr/bin/env bash
# Run `agy` (Gemini) as an LLM provider for the pipeline's `local_command` tier
# and for the red-team reviewer.
#
# Why this exists alongside scripts/codex_llm.sh: the red team earns its keep by
# disagreeing with whatever wrote the artifact. When one model both writes the
# résumé and reviews it, it marks its own homework and finds less. Pointing the
# generator at codex and the reviewer here keeps two different models on the
# job, and neither is metered.
#
# Prompt delivery is the awkward part. `agy` has no stdin path in print mode:
# `--input-format stream-json` needs a stream-json session and `-p` takes its
# prompt attached to the flag. So the prompt is read from stdin and handed over
# as a single `-p=...` argument. Red-team prompts run to ~96 KB against an
# ARG_MAX of 1 MB here, which fits with room to spare — but the check below is
# explicit rather than hopeful, because the failure mode of an over-long argv is
# a truncated review that still parses.
set -euo pipefail

prompt="$(cat)"

max_bytes="$(getconf ARG_MAX 2>/dev/null || echo 262144)"
# Leave a wide margin: argv also carries the environment and the other flags.
budget=$(( max_bytes / 2 ))
size=${#prompt}
if [ "$size" -gt "$budget" ]; then
    echo "prompt is ${size} bytes, over the ${budget}-byte argv budget for agy" >&2
    exit 1
fi

exec agy \
    --output-format text \
    --print-timeout "${JOB_HUNT_AGY_TIMEOUT:-9m}" \
    ${JOB_HUNT_AGY_MODEL:+--model "$JOB_HUNT_AGY_MODEL"} \
    -p="$prompt"
