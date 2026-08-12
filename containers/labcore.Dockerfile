# Reference analysis image: labcore plus the dataframe/plotting stack every
# project imports. Build plan §5.4.
#
# Build context is the REPO ROOT, not containers/, because pixi.toml and
# pixi.lock live at the root:
#     docker build -f containers/labcore.Dockerfile -t labcore .

FROM ghcr.io/prefix-dev/pixi:0.76.2 AS build

WORKDIR /app
COPY pixi.toml pixi.lock ./
COPY pyproject.toml README.md ./
COPY src ./src

# --locked, never --frozen. --locked aborts on a lock that is stale relative to
# pixi.toml; --frozen would proceed and silently ship a different resolution than
# the one committed. In CI the loud failure is the point.
RUN pixi install --locked -e prod

# A conda/pixi analysis env lands around 2 GB unstripped, mostly in things a
# runtime image never reads: compiled test suites, C headers, static archives,
# and __pycache__ that Python regenerates anyway. Stripping is what gets a
# scientific image from "too big to pull on a login node" to the 150-400 MB the
# build plan describes. Nothing removed here is importable at runtime.
RUN set -eux; \
    E=/app/.pixi/envs/prod; \
    find "$E" -name '__pycache__' -type d -prune -exec rm -rf {} + ; \
    find "$E" -name '*.pyc' -delete ; \
    find "$E" -name '*.pyo' -delete ; \
    find "$E" -name '*.a' -delete ; \
    find "$E" -type d -name 'tests' -prune -exec rm -rf {} + ; \
    find "$E" -type d -name 'test' -prune -exec rm -rf {} + ; \
    rm -rf "$E"/include \
           "$E"/share/doc "$E"/share/man "$E"/share/locale \
           "$E"/share/terminfo "$E"/share/gtk-doc \
           "$E"/lib/cmake "$E"/lib/pkgconfig ; \
    find "$E" -name '*.so*' -type f -exec strip --strip-unneeded {} + 2>/dev/null || true ; \
    du -sh "$E"

RUN pixi shell-hook -e prod -s bash > /shell-hook \
 && printf '#!/bin/bash\n' > /app/entrypoint.sh \
 && cat /shell-hook >> /app/entrypoint.sh \
 && echo 'exec "$@"' >> /app/entrypoint.sh


FROM ubuntu:24.04 AS production

# ubuntu, not alpine and not distroless. Nextflow's container requirements mandate
# bash >= 3.0 and `ps` inside the image — distroless has neither by design — and
# alpine's musl libc breaks glibc-linked bioconda binaries.
#
# fonts-liberation is not decoration: containers ship no Arial, and without a
# metric-compatible fallback matplotlib emits a findfont warning per draw call,
# which is dozens of lines per figure in a Slurm log.
RUN apt-get update \
 && apt-get install -y --no-install-recommends bash procps fonts-liberation ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# THE PREFIX PATH MUST BE IDENTICAL IN BOTH STAGES. conda/pixi environments are
# not relocatable: absolute paths are baked into binaries at link time, and
# Apptainer preserves the layout verbatim when it converts this image. Copying
# /app/.pixi/envs/prod anywhere else produces an image that runs under Docker and
# dies under Apptainer.
COPY --from=build /app/.pixi/envs/prod /app/.pixi/envs/prod
COPY --from=build --chmod=0755 /app/entrypoint.sh /app/entrypoint.sh

ENV MPLBACKEND=Agg

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "-c", "import labcore; print('labcore', labcore.__version__)"]
