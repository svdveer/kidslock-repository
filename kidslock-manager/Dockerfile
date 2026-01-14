ARG BUILD_FROM
FROM $BUILD_FROM

# Installeer Python en pip
RUN apk add --no-cache python3 py3-pip

# Installeer de libraries (met break-system-packages voor HA compatibiliteit)
RUN pip install --no-cache-dir --break-system-packages \
    flask \
    requests \
    paho-mqtt

# Kopieer de code naar de container
COPY . /app
WORKDIR /app

# Start het script
CMD [ "python3", "main.py" ]