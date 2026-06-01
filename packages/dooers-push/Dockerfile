FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

# The build context must be the monorepo `packages/` directory so the
# dooers-protocol sibling is reachable (a Dockerfile cannot COPY from
# outside its build context). Build it with the companion Cloud Build
# config, which sets the context correctly:
#
#   gcloud builds submit packages/ \
#     --config=packages/dooers-push/cloudbuild.yaml \
#     --substitutions=_IMAGE=<region>-docker.pkg.dev/<project>/<repo>/dooers-push:<tag>
#
# (Equivalent local build: `docker build -f dooers-push/Dockerfile -t <img> .`
# run from inside `packages/`.)
COPY dooers-protocol /tmp/dooers-protocol
COPY dooers-push/pyproject.toml .
COPY dooers-push/README.md .
COPY dooers-push/src ./src

# Install the local protocol first so the image carries exactly the
# version in this repo, then install the service with --no-sources so uv
# ignores the editable `tool.uv.sources` path (which only exists in a dev
# checkout, not in the image). dooers-protocol is already satisfied by the
# first install.
RUN uv pip install --system --no-cache /tmp/dooers-protocol \
 && uv pip install --system --no-cache --no-sources .

ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "dooers_push.main:app", "--host", "0.0.0.0", "--port", "8080"]
