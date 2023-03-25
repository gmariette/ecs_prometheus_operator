import logging
from logging.handlers import TimedRotatingFileHandler
import pathlib
import os
import requests
import sys

ecs_metadata_url = os.environ.get('ECS_CONTAINER_METADATA_URI_V4')
# Check if we are running under an ECS task.
if ecs_metadata_url is not None:
    r = requests.get(ecs_metadata_url).json()
    docker_id = r.get('DockerId')
    server_type = os.environ.get("SERVER_TYPE")
    stack_name_lower = os.environ.get("STACKNAME").lower()

    # Common logging
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(server_type.upper())
    log_level = logging.INFO
    # Logging conf -> logfile
    # log_path = f'/app/logs/{stack_name_lower}/{server_type}/{docker_id}/'
    # log_filename = f'{server_type}.log'
    # pathlib.Path(log_path).mkdir(parents=True, exist_ok=True)
    # file_handler = TimedRotatingFileHandler(f'{log_path}/{log_filename}', when='midnight', backupCount=2)
    # file_handler.setFormatter(formatter)
    # logger.setLevel(log_level)
    # logger.addHandler(file_handler)
    # Logging conf -> console
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.setLevel(log_level)
    logger.addHandler(console_handler)
else:
    logging.error('Unable to access the ECS_CONTAINER_METADATA_URI_V4')
    sys.exit(1)