FROM ubuntu:24.04
RUN apt-get update && apt-get install -y python3 python3-venv python3-pip \
    build-essential libssl-dev ca-certificates curl wget git sqlite3 && rm -rf /var/lib/apt/lists/*
RUN useradd -m -s /bin/bash -u 3000 kanthai
COPY --chown=kanthai:kanthai entrypoint.sh /usr/local/bin/hermes-entrypoint.sh
RUN chmod +x /usr/local/bin/hermes-entrypoint.sh
USER kanthai
WORKDIR /home/kanthai
ENV HOME=/home/kanthai
ENTRYPOINT ["/usr/local/bin/hermes-entrypoint.sh"]
