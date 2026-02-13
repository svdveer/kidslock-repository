FROM python:3.11-alpine

# Installeer systeem-afhankelijkheden
RUN apk add --no-cache iputils sqlite bash

WORKDIR /app

# Gebruik caching voor requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopieer de rest van de bestanden
COPY . .

# Maak de permanente data-map aan
RUN mkdir -p /data

# Maak poort 8000 kenbaar
EXPOSE 8000

# Zorg dat het start-script uitvoerbaar is
RUN chmod a+x run.sh

# Start de applicatie
CMD ["python", "main.py"]