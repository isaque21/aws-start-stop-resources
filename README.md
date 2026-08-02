# AWS Start-Stop Routines

Rotinas automatizadas de start/stop para recursos AWS, executadas via Lambda Functions com agendamento por tags.

## Recursos Suportados

| Diretório | Recurso | Descrição |
|-----------|---------|-----------|
| `start-stop-ec2/` | EC2 Instances | Liga e desliga instâncias EC2 |
| `start-stop-rds/` | RDS Instances | Liga e desliga instâncias RDS (exceto Aurora e DocumentDB) |
| `start-stop-rds-aurora/` | RDS Aurora Clusters | Liga e desliga clusters Aurora |
| `start-stop-ecs-fargate/` | ECS Fargate Services | Escala tasks para 0 (stop) ou restaura o desired count (start) |

## Como Funciona

Cada Lambda é disparada a cada 5 minutos via EventBridge Rule. Ela verifica as tags dos recursos e executa as ações de start/stop conforme o agendamento configurado.

### Tags Necessárias nos Recursos

| Tag | Valor | Descrição |
|-----|-------|-----------|
| `Scheduled` | `Active` | Ativa o gerenciamento para o recurso |
| `Period-N` | `Monday-Friday` | Define o intervalo de dias (suporta dia único ou range) |
| `ScheduleStart-N` | `08:00` | Horário para ligar o recurso |
| `ScheduleStop-N` | `18:00` | Horário para desligar o recurso |

O `N` é um identificador numérico que associa o período aos horários (ex: `Period-1`, `ScheduleStart-1`, `ScheduleStop-1`).

### Exemplo de Tags

```
Scheduled       = Active
Period-1        = Monday-Friday
ScheduleStart-1 = 08:00
ScheduleStop-1  = 20:00
Period-2        = Saturday
ScheduleStart-2 = 09:00
ScheduleStop-2  = 13:00
```

Neste exemplo, o recurso liga de segunda a sexta às 08:00 e desliga às 20:00. Aos sábados liga às 09:00 e desliga às 13:00.

## Variáveis de Ambiente

| Variável | Descrição |
|----------|-----------|
| `REGIONS` | Regiões AWS onde as rotinas atuam (separadas por vírgula) |
| `ALARMS_MANAGER` | `True` ou `False` — gerencia alarmes do CloudWatch junto com o start/stop |

Quando `ALARMS_MANAGER` está habilitado, a Lambda desabilita os alarmes vinculados ao recurso antes de desligá-lo e reabilita ao ligá-lo, evitando alertas falsos.

## ECS Fargate — Comportamento Específico

Para serviços ECS, a Lambda utiliza uma tag adicional:

| Tag | Descrição |
|-----|-----------|
| `DesiredCountTasks` | Quantidade de tasks para restaurar no start |

No stop, o desired count é setado para 0. No start, restaura o valor da tag `DesiredCountTasks`. A tag é atualizada automaticamente quando o desired count atual é maior que 0 e diferente do valor na tag.

## Deploy

O deploy é feito via CodeBuild na conta de gestão, que assume uma cross-account role na conta do cliente e atualiza o código das Lambdas. Veja o template CloudFormation em `brlink-cloudformation-start-stop/` para detalhes da infraestrutura.

## Estrutura do Repositório

```
.
├── start-stop-ec2/              # Lambda para EC2
├── start-stop-rds/              # Lambda para RDS
├── start-stop-rds-aurora/       # Lambda para RDS Aurora
├── start-stop-ecs-fargate/      # Lambda para ECS Fargate
├── brlink-cloudformation-start-stop/  # Templates CloudFormation
└── README.md
```
