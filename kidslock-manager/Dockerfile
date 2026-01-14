ARG BUILD_FROM
FROM $BUILD_FROM

# Installeer Python en benodigde systeempakketten
RUN apk add --no-update python3 py3-pip

# Installeer de Python bibliotheken die we gebruiken
RUN pip install --no-cache-dir \
    flask \
    requests \
    paho-mqtt

# Kopieer je code naar de container
COPY . /app
WORKDIR /app

# Start het script
CMD [ "python3", "main.py" ]