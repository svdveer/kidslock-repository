ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:latest
FROM $BUILD_FROM

# Systeem-afhankelijkheden
RUN apk add --no-cache python3 py3-pip iputils bash curl sqlite

WORKDIR /app

# Requirements installeren
COPY requirements.txt /tmp/
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt --break-system-packages

# Kopieer alles
COPY . /app

# GEEN CMD, GEEN ENTRYPOINT. 
# We laten Home Assistant de s6-overlay bepalen.