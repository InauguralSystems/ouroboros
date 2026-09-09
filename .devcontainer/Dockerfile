# Self-contained dev/CI image for ouroboros.
#
# ouroboros is the self-hosting EigenScript compiler — written in EigenScript,
# run against the runtime. This image builds EigenScript at a pinned tag and
# puts it on PATH, so the tag below IS the version pin — bump EIGS_REF to move
# the runtime under the compiler.
#
# The same image backs Codespaces AND CI (.github/workflows/tests.yml runs the
# suite inside it via devcontainers/ci), so "green in my Codespace" and "green
# in CI" can't drift. Building from source (rather than FROM a published
# runtime image) keeps this working for a private repo with no cross-repo
# package-pull credentials.
FROM debian:stable-slim

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        git \
        python3 \
 && rm -rf /var/lib/apt/lists/*

# Build + install EigenScript at the pinned ref onto /usr/local (binary on
# PATH, stdlib at /usr/local/lib/eigenscript — found relative to the binary).
# EIGS_REF may be a tag, branch, or commit SHA (fetched directly, so an
# immutable tag/SHA stays reproducible). The compilers mirror this release's
# call rule: a bare literal list after 'of' is always an argument list at every
# count (#405, v0.27.0), parens still pass one argument (#355, v0.24.0); plus
# statement terminator (#326), full-postfix prev/at + question-word fallbacks
# (#328), out-of-range OP_SET_LOCAL raises (#348), and integer-only hex
# lexed-not-strtod (v0.25.0). Bump this ARG and the frontend/codegen together
# (the deferred-mirror rule).
#
# v0.35.2: the release that fixes upstream #830 — #827 had narrowed assignment
# history to an armed-name set only src/compiler.c could populate, so the AOT
# (which emits C and never runs the bytecode compiler) got null from every
# `prev of` / `at`-qualified read. #832 re-based the filter on chunk
# provenance (EigsChunk.compiler_scanned) and made trace_assign the
# unconditional producer-facing entry point, which is what aot_rt.h already
# calls — so there is nothing to mirror on this side.
#
# v0.43.0 tag (stamp a6c50fb): three upstream changes this side mirrors (ouroboros#147).
# #1106 (EigenScript#1056): load_file/import resolve from the CONTAINING
# FILE's directory -> eigs_modules walk -> nearest eigs.json project root ->
# stdlib roots; the process cwd is no longer a step, which is what made the
# AOT's build-time splicing of literal load_file targets sound (the splicer
# now mirrors that chain statically). #1110 (EigenScript#1102): `report` and
# `report_value` are reserved observer forms -- parse error E005 as a binding
# or value or over a non-identifier operand -- mirrored in src/frontend.eigs
# (own token kind, every p_report_error arm) and pinned by test/run.sh's
# reject_one cases. #1113: embed observer gate default -- no mirror needed.
ARG EIGS_REF=v0.43.0
RUN mkdir -p /opt/eigenscript-src && cd /opt/eigenscript-src && git init -q \
 && git remote add origin https://github.com/InauguralSystems/EigenScript.git \
 && git fetch -q --depth 1 origin "${EIGS_REF}" && git checkout -q FETCH_HEAD \
 && make install PREFIX=/usr/local CC=gcc

# Keep the runtime SOURCE (not just the installed binary): the AOT toolchain
# (aot/build.sh) compiles a static lib from the runtime .c files and uses its
# headers, so the AOT harness can run in CI and in a Codespace. EIGS_DIR points
# build.sh at it; the on-PATH `eigenscript` binary (same ref) is the transpiler.
ENV EIGS_DIR=/opt/eigenscript-src

WORKDIR /workspaces/ouroboros
