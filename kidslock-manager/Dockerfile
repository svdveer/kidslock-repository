ARG BUILD_FROM
FROM $BUILD_FROM
RUN apk add --no-cache python3 py3-pip
WORKDIR /app
COPY . .
RUN pip3 install --no-cache-dir -r requirements.txt
RUN chmod a+x /app/run.sh
CMD [ "/app/run.sh" ]