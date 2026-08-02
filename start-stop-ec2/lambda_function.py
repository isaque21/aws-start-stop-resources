import boto3
import botocore
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

# Configurações via Variáveis de Ambiente
REGIONS_AWS = os.environ.get('REGIONS', 'us-east-1')
AWS_REGIONS = [r.strip() for r in REGIONS_AWS.split(',') if r.strip()]

# Normaliza a variável booleana para evitar erros de digitação (True/true/TRUE)
ALARMS_MANAGER = os.environ.get('ALARMS_MANAGER', 'False').strip().lower() == 'true'

# Fuso horário configurável via nome da região IANA (ex: 'America/Sao_Paulo')
# O ZoneInfo aplica automaticamente as regras de Horário de Verão
TZ_NAME = os.environ.get('TIMEZONE', 'UTC').strip()

DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

def is_time_in_window(current_time, scheduled_time_str):
    """
    Verifica se o horário agendado está dentro de uma janela de execução.
    Como a Lambda corre a cada 5 min, toleramos um atraso (-2 min) 
    e olhamos para a frente até ao final do ciclo (+4 min).
    """
    try:
        sched_time = datetime.strptime(scheduled_time_str.strip(), "%H:%M")
        current_mins = current_time.hour * 60 + current_time.minute
        sched_mins = sched_time.hour * 60 + sched_time.minute
        
        diff = sched_mins - current_mins
        
        # Considera uma janela: de 2 minutos no passado até 4 minutos no futuro
        return -2 <= diff <= 4
    except (ValueError, TypeError):
        return False

def safe_alarm_action(cloudwatch, action, alarm_names, max_retries=5):
    """Ativa ou desativa alarmes lidando com Throttling (Exponential Backoff)."""
    retries = 0
    while retries < max_retries:
        try:
            if action == 'disable':
                cloudwatch.disable_alarm_actions(AlarmNames=alarm_names)
            elif action == 'enable':
                cloudwatch.enable_alarm_actions(AlarmNames=alarm_names)
            return
        except botocore.exceptions.ClientError as e:
            if "Throttling" in str(e):
                sleep_time = 2 ** retries
                print(f"Throttling detetado! A tentar novamente em {sleep_time}s")
                time.sleep(sleep_time)
                retries += 1
            else:
                raise
    print(f"Falha ao realizar '{action}' nos alarmes {alarm_names} após tentativas.")

def manage_alarms(all_alarms, instance_id, action, cloudwatch):
    """Busca e gere os alarmes vinculados a uma instância específica."""
    alarms_to_manage = [
        alarm['AlarmName']
        for alarm in all_alarms
        for dim in alarm['Dimensions']
        if dim['Name'] == 'InstanceId' and dim['Value'] == instance_id
    ]
    
    if not alarms_to_manage:
        return

    print(f"Encontrados {len(alarms_to_manage)} alarmes para '{action}' na instância {instance_id}")
    for i in range(0, len(alarms_to_manage), 100):
        batch = alarms_to_manage[i:i+100]
        safe_alarm_action(cloudwatch, action, batch)
        print(f"Lote de alarmes '{action}' executado com sucesso.")

def lambda_handler(event, context):
    print(f'{"-"*40}\nA iniciar execução do agendamento...')
    
    # Define o tempo atual aplicando o fuso horário (com suporte a Horário de Verão)
    current_time = datetime.now(ZoneInfo(TZ_NAME))
    current_day = current_time.strftime("%A")
    print(f'Tempo Atual (Local): {current_time.strftime("%H:%M")} | Dia: {current_day}\n{"-"*40}')

    for region in AWS_REGIONS:
        print(f'A analisar a região: {region}')
        ec2 = boto3.client('ec2', region_name=region)
        cloudwatch = boto3.client('cloudwatch', region_name=region)

        stop_instances = []   
        start_instances = []
        all_alarms = []

        # 1. Recolhe TODOS os alarmes da região usando Paginator
        if ALARMS_MANAGER:
            cw_paginator = cloudwatch.get_paginator('describe_alarms')
            for page in cw_paginator.paginate():
                all_alarms.extend(page['MetricAlarms'])

        # 2. Filtra instâncias onde a CHAVE 'Scheduled' exista (ignora o valor nesta fase)
        ec2_paginator = ec2.get_paginator('describe_instances')
        filters = [{'Name': 'tag-key', 'Values': ['Scheduled']}]
        
        for page in ec2_paginator.paginate(Filters=filters):
            for reservation in page['Reservations']:
                for instance in reservation['Instances']:
                    
                    instance_id = instance['InstanceId']
                    instance_state = instance['State']['Name']
                    
                    # CORREÇÃO A: Limpa espaços em branco das Chaves e dos Valores
                    tags = {tag['Key'].strip(): tag['Value'].strip() for tag in instance.get('Tags', [])}
                    
                    # CORREÇÃO A: Valida se a tag Scheduled é 'active' (case-insensitive)
                    if tags.get('Scheduled', '').lower() != 'active':
                        continue
                    
                    periods = [key.split('-')[1] for key in tags if key.startswith('Period-')]

                    for p in periods:
                        period_value = tags.get(f'Period-{p}', '')
                        
                        # CORREÇÃO B: Normaliza os dias da semana (ex: 'monday' vira 'Monday')
                        days_range = [d.strip().capitalize() for d in period_value.split('-')]
                        
                        is_today_in_period = False
                        
                        # Validação do intervalo de dias
                        if len(days_range) == 2 and days_range[0] in DAYS and days_range[1] in DAYS:
                            start_idx, end_idx = DAYS.index(days_range[0]), DAYS.index(days_range[1])
                            curr_idx = DAYS.index(current_day)
                            
                            # Suporta intervalos que viram a semana (ex: Friday-Monday)
                            if start_idx <= end_idx:
                                is_today_in_period = start_idx <= curr_idx <= end_idx
                            else:
                                is_today_in_period = curr_idx >= start_idx or curr_idx <= end_idx
                        elif len(days_range) == 1 and current_day == days_range[0]:
                            is_today_in_period = True

                        if is_today_in_period:
                            # Avaliação para PARAR
                            stop_time_str = tags.get(f'ScheduleStop-{p}')
                            if stop_time_str and is_time_in_window(current_time, stop_time_str):
                                if instance_state == 'running':
                                    stop_instances.append(instance_id)
                                    print(f'[{instance_id}] Adicionada para PARAR.')
                            
                            # Avaliação para INICIAR
                            start_time_str = tags.get(f'ScheduleStart-{p}')
                            if start_time_str and is_time_in_window(current_time, start_time_str):
                                if instance_state == 'stopped':
                                    start_instances.append(instance_id)
                                    print(f'[{instance_id}] Adicionada para INICIAR.')

        # 3. Executar Paragens (Stop)
        if stop_instances:
            print(f'\nA parar instâncias na região {region}...')
            if ALARMS_MANAGER:
                for inst in stop_instances:
                    manage_alarms(all_alarms, inst, 'disable', cloudwatch)
            try:
                ec2.stop_instances(InstanceIds=stop_instances)
                print(f'Sucesso ao enviar comando de Stop para: {stop_instances}')
            except Exception as e:
                print(f'[Erro ao parar instâncias] {e}')
                if ALARMS_MANAGER:
                    for inst in stop_instances:
                        manage_alarms(all_alarms, inst, 'enable', cloudwatch)
        
        # 4. Executar Inícios (Start)
        if start_instances:
            print(f'\nA iniciar instâncias na região {region}...')
            if ALARMS_MANAGER:
                for inst in start_instances:
                    manage_alarms(all_alarms, inst, 'enable', cloudwatch)
            try:
                ec2.start_instances(InstanceIds=start_instances)
                print(f'Sucesso ao enviar comando de Start para: {start_instances}')
            except Exception as e:
                print(f'[Erro ao iniciar instâncias] {e}')
                if ALARMS_MANAGER:
                    for inst in start_instances:
                        manage_alarms(all_alarms, inst, 'disable', cloudwatch)
        
        print(f'{"-"*40}')

    return 'Success!'