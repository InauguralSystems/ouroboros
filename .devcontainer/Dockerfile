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
# immutable tag/SHA stays reproducible). Pinned to v0.19.0 — the release that
# ships the flat-buffer tensors (shaped VAL_BUFFER, #275) the AOT's zero-copy
# tensor view needs, plus the `dot`/`norm` reduction builtins (#272/#273).
ARG EIGS_REF=v0.21.0
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
