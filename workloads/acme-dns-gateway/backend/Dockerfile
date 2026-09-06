FROM python:3.12-alpine

RUN apk add --no-cache ca-certificates

COPY gateway.py /app/gateway.py

EXPOSE 8080
ENTRYPOINT ["python3", "/app/gateway.py"]
