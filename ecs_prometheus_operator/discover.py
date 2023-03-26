import boto3
import os
import time
import sys
import json
import signal
from copy import copy
from datetime import datetime
from setup_logger import logger

class ScrapExporters:
    def __init__(self, event_bus_name:str) -> None:
        self.region = os.environ.get('REGION')
        self.stackname = os.environ.get('STACKNAME')
        self.project, self.env_type, self.env_num = self.stackname.split('-')
        self.env_name = f"{self.env_type}-{self.env_num}".upper()
        self.cluster_name = f'{self.stackname}-ECS-CLUSTER'
        self.ecs_client = self.init_ecs_client()
        self.eventbridge_client = self.init_eventbridge_client()
        self.event_bus_arn = self.get_event_bus_arn(event_bus_name)
        self.reference_exporter_dict = {}
        self.current_exporter_dict = {}
        self.task_definition_port_reference = {}

    def terminate(self, signum, frame):
        logger.info(f'Received: {signum}, going to exit now')
        sys.exit(0)

    def init_ecs_client(self):
        return boto3.client('ecs', region_name=self.region)

    def init_eventbridge_client(self):
        return boto3.client('events', region_name=self.region)

    def get_event_bus_arn(self, name:str) -> str:
        response = self.eventbridge_client.list_event_buses()['EventBuses']
        for item in response:
            if item.get('Name') == name:
                logger.info('Found the event bus')
                return item.get('Arn')
        logger.error(f'No event bus with name {name} found!')
        sys.exit(1)

    def get_running_tasks(self) -> list:
        response = self.ecs_client.list_tasks(
            cluster=self.cluster_name,
            desiredStatus='RUNNING',
            launchType='EC2'
        )['taskArns']
        logger.info(f'Found {len(response)} runnings tasks')
        return response

    def get_tasks_detail(self, tasks:list) -> list:
        logger.info('Extracting tasks details')
        response = self.ecs_client.describe_tasks(
            cluster=self.cluster_name,
            tasks=tasks
        )['tasks']
        return response

    def get_task_def_exposed_port(self, task_definition_arn:str, container_name:str):
        # Here we build our cache - there is no need to call the API everytime as we don't push every minute
        if self.task_definition_port_reference.get(task_definition_arn) is None:
            self.task_definition_port_reference[task_definition_arn] = {}
        if self.task_definition_port_reference[task_definition_arn].get(container_name) is None:
            logger.info(f'{task_definition_arn=} not referenced for {container_name=}, using describe_task_definition api')
            response = self.ecs_client.describe_task_definition(
                taskDefinition=task_definition_arn
            )['taskDefinition']
            for container_definition in response.get('containerDefinitions'):
                if container_definition.get('name') == container_name:
                    self.task_definition_port_reference[task_definition_arn][container_name] = container_definition['portMappings'][0].get('hostPort')
        
        return self.task_definition_port_reference[task_definition_arn].get(container_name)

    def analyse_containers(self, tasks_details:list) -> None:
        logger.info('Analyzing tasks to identify the exporter containers')
        for task in tasks_details:
            task_name = task.get('taskDefinitionArn').split('/')[-1].split(':')[0]
            for container in task.get('containers'):
                container_name = container.get('name')
                if 'exporter' in container_name:
                    logger.info(f"Found container {container_name} in task {task_name}")
                    #We want to create our dictionary
                    if container_name not in self.current_exporter_dict.keys():
                        self.current_exporter_dict[container_name] = {
                            'ports': [],
                            'ips': []
                        }
                    try:
                        ip = container.get('networkInterfaces')[0].get('privateIpv4Address')
                    except IndexError:
                        logger.info(f"Container {container_name} does not have a network interface. {container.get('networkInterfaces')}")
                    exposed_port = self.get_task_def_exposed_port(task.get('taskDefinitionArn'), container_name)
                    if exposed_port not in self.current_exporter_dict[container_name]['ports']:
                        self.current_exporter_dict[container_name]['ports'].append(exposed_port)
                    self.current_exporter_dict[container_name]['ips'].append(ip)

    def discover(self):
        running_tasks = self.get_running_tasks()
        tasks_details = self.get_tasks_detail(running_tasks)
        self.analyse_containers(tasks_details)
        logger.info('Current env looks like this:')
        logger.info(self.current_exporter_dict)
        logger.info('Reference env looks like this:')
        logger.info(self.reference_exporter_dict)
        return self.current_exporter_dict

    def is_current_same_as_ref(self, d1:dict, d2:dict):
        return d1 == d2

    def identify_differences_between_dicts(self, current_dict:dict, ref_dict:dict):
        logger.info('Going to analyse which components are differents between our current env and the reference dict')
        differences = {}
        for item in current_dict:
            if current_dict.get(item) == ref_dict.get(item):
                continue
            else:
              differences[item] = current_dict.get(item)
        
        logger.info(f'Found those differences between our ref_dict and our current_dict: {differences}')

        logger.info('Going to check if some exporters needs to be purged (I.E some might not be running anymore)')

        diff_keys = list(set(ref_dict.keys()) - set(current_dict.keys()))
        if diff_keys:
            for item in diff_keys:
                logger.info(f'Adding key {item} as empty to purge our CRDs')
                differences[item] = {"port": [], "ips": []}

        return differences

    def create_event(self, event:dict) -> int:
        logger.info(f'Sending an event with {event=} on bus arn {self.event_bus_arn}')
        response = self.eventbridge_client.put_events(
            Entries=[
                {
                    'Time': datetime.now(),
                    'Source': 'ecs-prometheus-operator',
                    'Detail': json.dumps(event),
                    'DetailType': "ecs-prometheus-operator",
                    'EventBusName': self.event_bus_arn,
                },
            ]
        )['Entries'][0]
        logger.info(response)
        if response.get('EventId') is not None:
            logger.info(f"Event {response.get('EventId')} successfully created")
            return 1
        else:
            logger.error(f"Problem while creating event {event}!")
            return 0

    def create_events(self, exporters:dict):
        number_of_successfull_events = 0
        for i, exporter in enumerate(exporters, start=1):
            logger.info(f'Going to create new event for {exporter=}')
            number_of_successfull_events += self.create_event({f'{exporter.replace("-","")}-{self.project.lower()}-{self.env_name.lower()}': exporters[exporter]})

        logger.info(f"Successfully sent {number_of_successfull_events}/{i}")
        return number_of_successfull_events == i

    def reset_current_exporter_dict(self):
        logger.info('Resetting values for self.current_exporter_dict')
        self.current_exporter_dict = {}

    def main(self):
        logger.info('Discovering services...')
        self.discover()
        if len(self.current_exporter_dict):
            if self.is_current_same_as_ref(self.current_exporter_dict, self.reference_exporter_dict):
                logger.info('Current exporter are already declared')
                self.reset_current_exporter_dict()
            else:
                exporters_to_crud = self.identify_differences_between_dicts(self.current_exporter_dict, self.reference_exporter_dict)
                # FIXME: We need to catch the case when the reference exp dict contains more than the current env - this will allow purging
                # GOTO: function identify_differences_between_dicts to fix
                if len(exporters_to_crud) > 0:
                    if self.create_events(exporters_to_crud):
                        logger.info('Saving current exporter state')
                        self.reference_exporter_dict = copy(self.current_exporter_dict)
                        self.reset_current_exporter_dict()
                    else:
                        logger.error("Problem while creating event, we don't save our conf!")
        else:
            logger.info('Did not found any exporter')

if __name__ == "__main__":
    scraper = ScrapExporters(event_bus_name='default')
    signal.signal(signal.SIGTERM, scraper.terminate())
    while True:
        scraper.main()
        time.sleep(60)
