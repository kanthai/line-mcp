FROM node:22-slim
RUN apt-get update && apt-get install -y python3 python3-pip curl wget git \
    libssl-dev ca-certificates && rm -rf /var/lib/apt/lists/*
RUN useradd -m -s /bin/bash -u 1000 kanthai
USER kanthai
WORKDIR /home/kanthai
ENV HOME=/home/kanthai
CMD ["node", "/home/kanthai/.npm-global/lib/node_modules/openclaw/openclaw.mjs"]
