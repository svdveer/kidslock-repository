ARG BUILD_FROM
FROM $BUILD_FROM

# Installeer Python en pip
RUN apk add --no-cache python3 py3-pip

# Installeer FastAPI, Uvicorn en andere benodigdheden
RUN pip install --no-cache-dir --break-system-packages \
    fastapi \
    uvicorn \
    jinja2 \
    python-multipart \
    requests \
    paho-mqtt

# Kopieer code naar de container
COPY . /app
WORKDIR /app

# Start de applicatie
CMD [ "python3", "main.py" ]