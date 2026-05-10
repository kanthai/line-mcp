FROM ubuntu:24.04
RUN apt-get update && apt-get install -y python3 python3-venv python3-pip \
    libssl-dev ca-certificates curl wget git sqlite3 && rm -rf /var/lib/apt/lists/*
RUN useradd -m -s /bin/bash -u 1000 kanthai
USER kanthai
WORKDIR /home/kanthai
ENV HOME=/home/kanthai
CMD ["/home/kanthai/.hermes/hermes-agent/venv/bin/hermes"]
