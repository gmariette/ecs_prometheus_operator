FROM python:slim-bullseye

# Metadata of the Dockerfile
LABEL maintainer="Mariette Geoffrey" \
      version="1.0" \
      description="This is a python ECS Task exporter docker file"

ENV version="latest"

COPY ./python_ecstasks_exporter/* /app/
RUN apt-get update && \
    apt-get install curl procps -y && \
    pip install --upgrade pip && \
    pip install -r /app/requirements.txt

WORKDIR /app

CMD ["python", "discover.py"]
