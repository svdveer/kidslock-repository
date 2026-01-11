ARG BUILD_FROM
FROM $BUILD_FROM

# Installeer Python en benodigde systeembestanden
RUN apk add --no-cache python3 py3-pip

WORKDIR /app

# Kopieer requirements eerst (optimaliseert Docker caching)
COPY requirements.txt .

# Installeer de Python libraries met de override vlag
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# Kopieer de rest van de bestanden
COPY . .

# Maak het opstartscript uitvoerbaar
RUN chmod a+x /app/run.sh

CMD [ "/app/run.sh" ]