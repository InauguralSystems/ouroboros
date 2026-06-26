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
 && rm -rf /var/lib/apt/lists/*

# Build + install EigenScript at the pinned ref onto /usr/local (binary on
# PATH, stdlib at /usr/local/lib/eigenscript — found relative to the binary).
# EIGS_REF may be a tag, branch, or commit SHA (fetched directly, so an
# immutable SHA stays reproducible). Pinned to the `dot` reduction builtin
# (EigenScript #272), needed by the AOT's reassociated SIMD dot.
ARG EIGS_REF=e984dbd8db95077f7947aae9f4827a8798e64f67
RUN mkdir -p /tmp/eigs && cd /tmp/eigs && git init -q \
 && git remote add origin https://github.com/InauguralSystems/EigenScript.git \
 && git fetch -q --depth 1 origin "${EIGS_REF}" && git checkout -q FETCH_HEAD \
 && make -C /tmp/eigs install PREFIX=/usr/local CC=gcc \
 && rm -rf /tmp/eigs

WORKDIR /workspaces/ouroboros
