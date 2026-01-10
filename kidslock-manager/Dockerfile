ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:latest
FROM $BUILD_FROM

# Systeem-afhankelijkheden installeren
RUN apk add --no-cache \
    python3 \
    py3-pip \
    iputils \
    bash \
    curl \
    sqlite

WORKDIR /app

# Installeer requirements
COPY requirements.txt /tmp/
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt --break-system-packages

# Kopieer de rest van de bestanden naar de container
COPY . /app

# Maak het script uitvoerbaar
RUN chmod a+x /app/run.sh

# BELANGRIJK: Geen CMD gebruiken! 
# De base image regelt het opstarten via de s6-overlay.