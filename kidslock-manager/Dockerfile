ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.11-alpine3.19
FROM $BUILD_FROM

# Systeem-afhankelijkheden (iputils voor ping, sqlite voor database)
RUN apk add --no-cache iputils bash curl sqlite

WORKDIR /app

# Installeer requirements
COPY requirements.txt /tmp/
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt

# Kopieer de rest (inclusief je templates map!)
COPY . /app

RUN chmod a+x /app/run.sh

CMD [ "/app/run.sh" ]