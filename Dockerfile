ARG BUILD_FROM
FROM $BUILD_FROM

# Install Python and pip
RUN apk add --no-cache python3 py3-pip

# Copy app data
COPY . /app
WORKDIR /app

# Install Python dependencies
RUN pip3 install fastapi uvicorn jinja2 requests paho-mqtt --break-system-packages

# Copy and execute run script
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]