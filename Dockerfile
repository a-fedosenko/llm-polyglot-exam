# One image, one process: FastAPI serves both the API and the built UI, so
# there is no second service, no reverse proxy, and no CORS story to get wrong.

# ---- the UI ----------------------------------------------------------------
# Two things are pinned here and both were found the hard way, because both
# fail as "Missing: @emnapi/... from lock file" — a message that reads like a
# corrupt lockfile and is neither time.
#
#   Debian, not alpine.  Tailwind v4 and lightningcss ship native binaries whose
#                        optional dependencies resolve per libc, so a lockfile
#                        made on glibc cannot satisfy `npm ci` on musl.
#   node 24, not 22.     npm 11 (node 24) and npm 10 (node 22) disagree about
#                        which entries of the wasm32-wasi optional tree belong
#                        in the lockfile. Match whatever generated the lockfile.
FROM node:24-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# vite.config.ts writes to ../backend/static, which is right for a developer
# working in the repo and wrong here; --outDir keeps the output inside the stage.
RUN npm run build -- --outDir dist --emptyOutDir

# ---- the app ---------------------------------------------------------------
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/polyglot backend/polyglot

# **Editable, deliberately.** `paths.ROOT` is computed from this module's own
# location, so a non-editable install would resolve the repository root to
# somewhere inside site-packages and the schemes, markers and specs would all
# be looked for in the wrong place — silently, since a missing control file
# reads as "this language cannot be verified" rather than as a broken install.
# The `lid` extra is not optional in here, whatever pyproject calls it.
# Without fasttext the gate checks the writing system and not the language --
# a model answering in Indonesian when asked for Acehnese passes -- and it does
# that silently. An image that can never identify a language, serving a page
# that offers to measure them, is the exact failure this project keeps warning
# about: when the instrument is missing, say so rather than quietly answering
# worse. On the host it stays an extra, because browsing results needs none of it.
RUN pip install -e "./backend[lid]"

# Authored assets: the instrument, version-controlled alongside the code.
COPY schemes schemes
COPY markers markers

# These three live under ./data, which is also the bind mount. Baking them in
# means `docker run` with no volume still works — Docker seeds an anonymous
# volume from the image — while compose's bind mount supplies the host's copies
# instead. Both are the repository's own files; they are tracked in git.
COPY data/specs data/specs
COPY data/controls data/controls
COPY data/unicode data/unicode

COPY --from=ui /ui/dist backend/static

# `data` is the only writable path, so a models.json anywhere else resolves on
# the host and vanishes in here — a failure that reads as "my file is right
# there" unless something says so.
VOLUME ["/app/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health').read()"

CMD ["polyglot", "serve", "--host", "0.0.0.0", "--port", "8000"]
