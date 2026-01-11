FROM python:3.11-alpine
RUN apk add --no-cache iputils sqlite bash
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN chmod a+x run.sh
CMD ["python", "main.py"]