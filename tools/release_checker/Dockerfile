FROM python:3.13.7-slim-bookworm@sha256:adafcc17694d715c905b4c7bebd96907a1fd5cf183395f0ebc4d3428bd22d92d AS oasdiff-download

ARG TARGETARCH
ARG OASDIFF_VERSION=1.28.0

# SHA-256 values are the exact linux_amd64/linux_arm64 tar.gz entries from the official v1.28.0 checksums.txt.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && case "${TARGETARCH}" in \
         amd64) OASDIFF_SHA256="e0ef076f2cf953d922addc04be9c3851cf3ec18f7678d2b94d44cea23dca51b5" ;; \
         arm64) OASDIFF_SHA256="cb15a381472321ac602cc252e65018d03feba7e6449a0854e1181680444d4051" ;; \
         *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
       esac \
    && curl -fsSL \
         "https://github.com/oasdiff/oasdiff/releases/download/v${OASDIFF_VERSION}/oasdiff_${OASDIFF_VERSION}_linux_${TARGETARCH}.tar.gz" \
         -o /tmp/oasdiff.tar.gz \
    && echo "${OASDIFF_SHA256}  /tmp/oasdiff.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/oasdiff.tar.gz -C /tmp oasdiff \
    && install -m 0755 /tmp/oasdiff /oasdiff \
    && test "$(/oasdiff --version)" = "oasdiff version ${OASDIFF_VERSION}"

FROM python:3.13.7-slim-bookworm@sha256:adafcc17694d715c905b4c7bebd96907a1fd5cf183395f0ebc4d3428bd22d92d AS runtime

ENV PYTHONPATH=/opt
COPY --from=oasdiff-download /oasdiff /usr/local/bin/oasdiff
COPY release_checker.py /opt/tools/release_checker/release_checker.py

FROM runtime AS testing

ARG PYTEST_VERSION=9.0.3
RUN pip install --no-cache-dir "pytest==${PYTEST_VERSION}"
COPY tests /opt/tools/release_checker/tests
ENTRYPOINT ["python", "-m", "pytest"]

FROM runtime AS production

ENTRYPOINT ["python", "/opt/tools/release_checker/release_checker.py"]
