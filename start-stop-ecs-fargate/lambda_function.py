# Start/Stop Tasks ECS periodically and Enable/Disable CloudWatch alarms.

# This function allows you to create startup/stop routines for ECS Fargate Clusters on different days and times and enable/disable CloudWatch alarms corresponding to the Services that suffered the action.

import boto3
import os
from datetime import datetime, timedelta

# Define AWS Region
REGIONS_AWS = os.environ['REGIONS']
REGIONS = REGIONS_AWS.replace(' ', '')
AWS_REGIONS = REGIONS.split(',')

# Define Disable/Enable 
ALARMS_MANAGER = os.environ['ALARMS_MANAGER']

DAYS = [
    'Sunday',
    'Monday',
    'Tuesday',
    'Wednesday',
    'Thursday',
    'Friday',
    'Saturday'
]

def manage_alarms(list_alarms, service_name, service_identifier, action, cloudwatch):
    print(f'----------------------------------------')
    print(f'Checking alarms for service {service_name}.')
    for alarms in list_alarms:
        for dimensions in alarms['Dimensions']:
            if dimensions['Name'] == service_identifier and dimensions['Value'] == service_name:
                if action == 'disable':
                    cloudwatch.disable_alarm_actions(AlarmNames=[alarms['AlarmName']])
                    print(f'Alarm {alarms['AlarmName']} is disabled!')
                elif action == 'enable':
                    cloudwatch.enable_alarm_actions(AlarmNames=[alarms['AlarmName']])
                    print(f'Alarm {alarms['AlarmName']} is enabled!')
    print(f'----------------------------------------')

# Update Service desired count
def update_service_desired_count(service, action, ecs):
    
    arn_part = service.split('/')

    service_tags = ecs.list_tags_for_resource(resourceArn=service)
    tags = service_tags['tags']

    # Check if tag exists
    tag_desired_count_tasks = next((tag['value'] for tag in tags if tag['key'] == 'DesiredCountTasks'), None)

    if tag_desired_count_tasks:
        desired_count = tag_desired_count_tasks.strip()

        if action == 'start':
            try:
                response = ecs.update_service(
                    cluster=arn_part[1],
                    service=arn_part[2],
                    desiredCount=int(desired_count)
                )
                print(f"Starting tasks in service {service.split('/')[2]}")
            except Exception as e:
                print(f"Failed to start tasks in service {service.split('/')[2]}: {str(e)}")
        elif action == 'stop':
            try:
                response = ecs.update_service(
                    cluster=arn_part[1],
                    service=arn_part[2],
                    desiredCount=0
                )
                print(f"Stopping tasks in service {service.split('/')[2]}")
            except Exception as e:
                print(f"Failed to stop tasks in service {service.split('/')[2]}: {str(e)}")

    else:
        print(f'Failed to update Service! Tag [DesiredCountTasks] not found.')

# Update current tag desired count
def set_current_desired_tag(ecs, service, current_desired):
    try:
        response = ecs.tag_resource(
        resourceArn=service,
        tags=[
            {
                'key': 'DesiredCountTasks',
                'value': str(current_desired)
            },
        ]
        )
    except Exception as e:
        print(f"Failed to update tag DesiredCountTasks: {str(e)}")

def lambda_handler(event, context):
    
    print(f'----------------------------------------')

    # Get actual local time
    current_time = datetime.now() - timedelta(hours=3)
    current_time_local = current_time.strftime("%H:%M")
    current_day = current_time.strftime("%A")
    print(f'Current time: {current_time_local}')
    print(f'Current day: {current_day}')

    # Iterate over each region
    for region in AWS_REGIONS:

        # Define ECS client connection
        ecs = boto3.client('ecs', region_name=region)

        # Define CloudWatch client connection
        cloudwatch = boto3.client('cloudwatch', region_name=region)

        # Deacribe all ECS clusters
        all_clusters = ecs.list_clusters()

        # Set empty lists
        all_alarms = []
        all_services = []

        stop_alarms_services = []
        start_alarms_services = []

        stop_tasks = []   
        start_tasks = []

        cw_next_token = None
        sv_next_token = None

        # Get all account alarms
        while True:
            if cw_next_token:
                response  = cloudwatch.describe_alarms(NextToken=cw_next_token)
            else:
                response  = cloudwatch.describe_alarms()

            all_alarms.extend(response['MetricAlarms'])

            cw_next_token = response.get('NextToken')
            
            if not cw_next_token:
                break

        for clusters in all_clusters['clusterArns']:

            print(f'----------------------------------------')
            print(f"Checking cluster {clusters.split('/')[1]} in {region}.")

            # Get all services for current cluster
            while True:
                if sv_next_token:
                    response  = ecs.list_services(cluster=clusters,nextToken=sv_next_token)
                else:
                    response  = ecs.list_services(cluster=clusters)

                all_services = response['serviceArns']

                if all_services:

                    print(f'Checking services in the cluster {clusters.split('/')[1]}: {all_services}')

                    for services in all_services:
                        # describes all services in detail
                        services_desc = ecs.describe_services(
                            cluster=clusters,
                            services=[
                                services,
                                ]
                        )

                        for service in services_desc['services']:
                                service_name = service['serviceName']
                                service_status = service['status']
                                current_desired = service['desiredCount']

                        print(f'----------------------------------------')
                        print(f"Service: {services.split('/')[2]}")

                        period = []
                        i = 0
                        j = 0
                        scheduled = 'Inactive'
                        current_desired_tag = None

                        # List tags for current Service
                        service_tags = ecs.list_tags_for_resource(resourceArn=services)
                        tags = service_tags['tags']

                        # Check if tag exists
                        tag_desired_count_tasks = next((tag['value'] for tag in tags if tag['key'] == 'DesiredCountTasks'), None)

                        if tag_desired_count_tasks:
                            current_desired_tag = int(tag_desired_count_tasks)
                        else:
                            set_current_desired_tag(ecs, services, current_desired)
                            
                        for tag in tags:
                            if 'Scheduled' in tag['key']:
                                scheduled = tag['value'].strip()
                        
                        print(f'Scheduled: {scheduled}')

                        # set current number of tasks in service tag
                        if current_desired > 0 and current_desired != current_desired_tag:
                            set_current_desired_tag(ecs, services, current_desired)

                        if scheduled == 'Active':
                            # Get all Period tag keys (e.g. Period-1, Period-2, ...) 
                            for tag in tags:      
                                if 'Period' in tag['key']:
                                    period.append(tag['key'].split('-')[1])
                                    i = i+1

                            # Get all Period tag values (e.g. Sunday-Saturday, Monday-Friday, ...) 
                            while j < i:
                                for tag in tags:
                                    if tag['key'] == 'Period-' + str(period[j]):
                                        numPeriod = tag['value'].strip()       
                                        print(f'Period: {numPeriod}')
                                        day = numPeriod.split('-')
                                        # print(f'Days: {day}')

                                # Add instance in array to stop
                                for tag in tags:
                                    if tag['key'] == 'ScheduleStop-' + str(period[j]):
                                        
                                        # Checks if the period has a range of days
                                        if len(day) > 1:
                                            
                                            # Check if the current day is within the period
                                            if DAYS.index(current_day) in range(DAYS.index(day[0]), DAYS.index(day[1]) + 1):
                                                print(f'{current_day} is on Stop Period-{period[j]}')
                                                
                                                if tag['value'] == current_time_local:
                                                    print(f'{services.split("/")[2]} is on the Stop time')

                                                    if service_status == 'ACTIVE':
                                                        stop_tasks.append(services)
                                                        stop_alarms_services.append(service_name)
                                                    else:
                                                        print(f'The {services.split("/")[2]} was not added to the stop tasks list because its state is: {service_status}.')
                                                        
                                            else:
                                                print(f'{current_day} is not on Stop Period-{period[j]}')
                                        else:

                                            # Checks if the period has a sigle day
                                            if current_day == day[0]:
                                                if tag['value'] == current_time_local:
                                                        print(f'{services.split("/")[2]} is on the Stop time')

                                                        if service_status == 'ACTIVE':
                                                            stop_tasks.append(services)
                                                            stop_alarms_services.append(service_name)
                                                        else:
                                                            print(f'The {services.split("/")[2]} was not added to the stop tasks list because its state is: {service_status}.')
                                                            
                                            else:
                                                print(f'{current_day} is not on Stop Period-{period[j]}')

                                # Add instance in array to start
                                for tag in tags:
                                    if tag['key'] == 'ScheduleStart-' + str(period[j]):
                                        
                                        # Checks if the period has a range of days
                                        if len(day) > 1:
                                            
                                            # Check if the current day is within the period
                                            if DAYS.index(current_day) in range(DAYS.index(day[0]), DAYS.index(day[1]) + 1):
                                                print(f'{current_day} is on Start Period-{period[j]}')
                                                
                                                if tag['value'] == current_time_local:
                                                    print(f'{services.split("/")[2]} is on the Start time')

                                                    if service_status == 'ACTIVE':
                                                        start_tasks.append(services)
                                                        start_alarms_services.append(service_name)
                                                    else:
                                                        print(f'The {services.split("/")[2]} was not added to the start tasks list because its state is: {service_status}.')
                                                        
                                            else:
                                                print(f'{current_day} is not on Start Period-{period[j]}')
                                        else:

                                            # Checks if the period has a sigle day
                                            if current_day == day[0]:
                                                if tag['value'] == current_time_local:
                                                        print(f'{services.split("/")[2]} is on the Start time')

                                                        if service_status == 'ACTIVE':
                                                            start_tasks.append(services)
                                                            start_alarms_services.append(service_name)
                                                        else:
                                                            print(f'The {services.split("/")[2]} was not added to the start tasks list because its state is: {service_status}.')
                                                            
                                            else:
                                                print(f'{current_day} is not on Start Period-{period[j]}')
                                j = j+1
                else:
                    print(f'No services found in cluster {clusters.split('/')[1]}.')

                # Get next token for listing all services
                sv_next_token = response.get('nextToken')
                if not sv_next_token:
                    break

        # Stop all tasks from services tagged to stop.
        if len(stop_tasks) > 0:
            print(f'----------------------------------------')
            for stop_task in stop_tasks:
                if ALARMS_MANAGER == 'True':
                    manage_alarms(all_alarms, stop_task.split('/')[2], 'ServiceName', 'disable', cloudwatch)
                try:
                    update_service_desired_count(stop_task, 'stop', ecs)
                    print(f'----------------------------------------')
                except Exception as e:
                    print (f'[Cannot stop tasks from service {stop_task.split('/')[2]}] {e}')
                    if ALARMS_MANAGER == 'True':
                        manage_alarms(all_alarms, stop_task.split('/')[2], 'ServiceName', 'enable', cloudwatch)
                
        else:
            print(f'----------------------------------------')
            print(f'No tasks to stop in {region}.')
            
        # Start tasks from services tagged to start. 
        if len(start_tasks) > 0:
            print(f'----------------------------------------')
            for start_task in start_tasks:
                if ALARMS_MANAGER == 'True':
                    manage_alarms(all_alarms, start_task.split('/')[2], 'ServiceName', 'enable', cloudwatch)
                try:
                    update_service_desired_count(start_task, 'start', ecs)
                    print(f'----------------------------------------')
                except Exception as e:
                    print (f'[Cannot start tasks from service {start_task.split('/')[2]}] {e}')
                    if ALARMS_MANAGER == 'True':
                        manage_alarms(all_alarms, start_task.split('/')[2], 'ServiceName', 'disable', cloudwatch)
                
        else:
            print(f'----------------------------------------')
            print(f'No tasks to start in {region}.')

    print(f'----------------------------------------')
    
    return 'Success!'