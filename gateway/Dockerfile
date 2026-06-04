FROM nousresearch/hermes-agent:latest

RUN chmod -R o+w /opt/hermes/scripts && \
    addgroup --gid 1000 runner && \
    adduser --uid 1000 --gid 1000 --disabled-password --gecos "" --home /opt/data runner
