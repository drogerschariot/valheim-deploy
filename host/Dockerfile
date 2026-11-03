# The "local" runtime: one image, one container per server, the game itself
# living on a named volume so it survives the container being replaced.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl libatomic1 libpulse-dev libpulse0 \
      lib32gcc-s1 lib32stdc++6 python3 tar util-linux \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --system valheim \
 && useradd --system --gid valheim --home-dir /opt/valheim --shell /usr/sbin/nologin valheim \
 && install -d -o valheim -g valheim /opt/valheim /opt/valheim/steamcmd /opt/valheim/instances \
 && install -d /opt/valheim/app /opt/valheim/bin /etc/valheim

ADD --chown=valheim:valheim https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz /tmp/steamcmd.tgz
RUN tar -xzf /tmp/steamcmd.tgz -C /opt/valheim/steamcmd \
 && chown -R valheim:valheim /opt/valheim/steamcmd && rm /tmp/steamcmd.tgz

COPY a2s.py agent.py launcher.py /opt/valheim/app/
COPY valheim-backup.sh valheim-update.sh /opt/valheim/bin/
COPY docker-entrypoint.py /opt/valheim/app/
RUN chmod 0755 /opt/valheim/bin/*.sh

# Docker's default stop signal is the one Valheim ignores.
STOPSIGNAL SIGINT
ENTRYPOINT ["python3", "/opt/valheim/app/docker-entrypoint.py"]
