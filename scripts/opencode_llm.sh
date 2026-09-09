#!/usr/bin/env bash
# Run `opencode run` as an LLM provider for the pipeline's `local_command` tier.
#
# The third wrapper, alongside scripts/codex_llm.sh and scripts/agy_llm.sh, so
# that a tier has somewhere to go when codex's daily quota runs out mid-batch
# (2026-09-08: it did, at job two of a re-run, and the job degraded to a 0.0
# stub). Each of the three meters separately, so the batch survives one of them
# being empty.
#
# Two things about `opencode run` shape this wrapper:
#
# 1. It has NO usable default model. With no -m it fails with an opaque
#    `UnknownError / Unexpected server error` AND EXITS 0 — the same silent
#    success-with-no-answer that made the agy wrapper hand empty strings to a
#    résumé node. So the model is always passed explicitly, and the non-empty
#    check at the bottom is what actually catches the failure.
#
# 2. Its default text output carries a banner (`> build · <model>`) and ANSI
#    escapes around the answer. `--format json` emits one JSON event per line
#    instead, and the answer is exactly the `text` events' `part.text` in
#    order — a real parse rather than trimming decoration off a stream.
#
# The default model is MiniMax-M3 on the operator's coding plan. Two things
# about that are easy to get wrong:
#
# - It is NOT the same path as the `minimax` HTTP tier in settings.example.yml.
#   That tier talks to the API directly with a max_tokens ceiling, and M3 is a
#   reasoning model that spends 2-3x the tokens on the same task -- which is
#   how a nine-job batch died on truncated `score_and_recommend` output on
#   2026-09-07. opencode drives the model through its own session and does not
#   hand the pipeline a half-finished answer, which is the whole reason this
#   route is worth having. Do not "fix" this back to the HTTP tier's model.
#
# - The red team (`mmx`, unless JOB_HUNT_REDTEAM_CMD says otherwise) is also
#   MiniMax. That is fine while this wrapper is the CHEAP tier: the reviewer's
#   job is to disagree with whatever WROTE the artifact, and artifacts come
#   from the premium tier. If opencode is ever moved to premium, move the red
#   team off mmx in the same change, or MiniMax marks its own homework.
#
# `opencode models` lists what else is reachable. `deepseek/*` is metered per
# token and is not to be used (2026-09-07: an unauthorised paid tier cost ~$17
# before anyone weighed it).
set -euo pipefail

model="${JOB_HUNT_OPENCODE_MODEL:-minimax-cn-coding-plan/MiniMax-M3}"

# opencode is an agent with a working directory, and this operator's config
# already allows every tool inside it. The prompts are self-contained --
# artifacts, JD and ground truth are all inlined -- so the run is given an
# empty scratch directory rather than the repo: nothing here needs reading,
# and anything the model decides to write lands somewhere disposable instead
# of on top of a profile or an output directory. `--auto` goes with that
# choice, not against it: reaching outside the working directory is an "ask"
# permission, and in headless mode an unanswered ask is another silent hang.
workdir="$(mktemp -d -t opencode-llm)"
trap 'rm -rf "$workdir"' EXIT

answer="$(
    opencode run \
        --model "$model" \
        --format json \
        --dir "$workdir" \
        --pure \
        --auto \
        2>/dev/null \
    | jq -j -R 'fromjson? // empty | select(.type == "text") | .part.text'
)"

if [ -z "$answer" ]; then
    echo "opencode run ($model) produced no answer" >&2
    exit 1
fi

printf '%s' "$answer"
