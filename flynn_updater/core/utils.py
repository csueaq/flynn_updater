import datetime
import boto3
import json
import requests
import io
from django.conf import settings
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)
asg = boto3.client('autoscaling')
ec2 = boto3.resource('ec2')
dns = boto3.client('route53')
rds = boto3.client('rds')
elb = boto3.client('elb')
s3 = boto3.client('s3')
s3_res = boto3.resource('s3')


def get_instances(asg_id: list):
    asg_instances = asg.describe_auto_scaling_groups(AutoScalingGroupNames=asg_id)['AutoScalingGroups'][0]['Instances']
    return asg_instances


def get_instance_state(instance):
    return ec2.Instance(instance['InstanceId']).state


def get_instances_by_state(instances: list, state: str = 'running'):
    instances_by_state = []
    for instance in instances:
        if state in get_instance_state(instance)['Name']:
            instances_by_state.append(instance)
    return instances_by_state


def get_instance_public_addr(instances: list):
    instances_addr = []
    for instance in instances:
        instances_addr.append(ec2.Instance(instance['InstanceId']).public_ip_address)
    return instances_addr


def get_instance_private_addr(instances: list):
    instances_addr = []
    for instance in instances:
        instances_addr.append(ec2.Instance(instance['InstanceId']).private_ip_address)
    return instances_addr


def dns_update(zone_id, records: list, domain, record_type='A', ttl=60):
    return dns.change_resource_record_sets(
        HostedZoneId=zone_id,
        ChangeBatch={
            'Comment': 'Auto DNS update @ %s' % str(datetime.datetime.now()),
            'Changes': [{
                'Action': 'UPSERT',
                'ResourceRecordSet': {
                    'Name': domain,
                    'Type': record_type,
                    'TTL': ttl,
                    'ResourceRecords': records
                }
            }]
        }
    )


def get_discovery_instances(discovery_token):
    instances = requests.get('%s/%s/instances' % (settings.FLYNN_DISCOVERY_URL, discovery_token)).json()
    return instances


def update_discovery_instances(discovery_token, instance_data: dict):
    headers = {'Content-Type': 'application/json'}
    return requests.post('%s/%s/instances' % (settings.FLYNN_DISCOVERY_URL, discovery_token), data=json.dumps(instance_data), headers=headers)


def get_rds_endpoint(rds_id):
    return rds.describe_db_instances(DBInstanceIdentifier=rds_id)['DBInstances'][0]['Endpoint']['Address']


def get_rds_security_group(rds_id):
    return rds.describe_db_instances(DBInstanceIdentifier=rds_id)['DBInstances'][0]['VpcSecurityGroups'][0]['VpcSecurityGroupId']


def get_security_group_rules(sg_id):
    security_group = ec2.SecurityGroup(sg_id)
    return security_group.ip_permissions


def add_security_group_rule(sg_id, ip, port, proto='tcp'):
    security_group = ec2.SecurityGroup(sg_id)
    rules = get_security_group_rules(sg_id)
    for rule in rules:
        if '%s/32' % ip not in [i['CidrIp'] for i in rule['IpRanges']]:
            security_group.authorize_ingress(
                IpProtocol=proto,
                FromPort=port,
                ToPort=port,
                CidrIp='%s/32' % ip
            )


def remove_security_group_rule(sg_id, ip, port, proto='tcp'):
    security_group = ec2.SecurityGroup(sg_id)
    rules = get_security_group_rules(sg_id)
    for rule in rules:
        if '%s/32' % ip in [i['CidrIp'] for i in rule['IpRanges']] and port is rule['ToPort']:
            security_group.revoke_ingress(
                IpProtocol=proto,
                FromPort=port,
                ToPort=port,
                CidrIp='%s/32' % ip
            )


def get_route53_records(zone_id, domain, record_type='A'):
    return dns.test_dns_answer(HostedZoneId=zone_id, RecordName=domain, RecordType=record_type)['RecordData']


def register_instances_with_elb(elb_id, instances: list):
    instances_list = []
    for i in instances:
        instances_list.append({'InstanceId': i})
    return elb.register_instances_with_load_balancer(
        LoadBalancerName=elb_id,
        Instances=instances_list
    )


def flynn_backup_to_s3(s3_bucket):
    backup = requests.get('https://controller.%s/backup?key=%s' % (settings.AWS_ROUTE53_DOMAIN, settings.FLYNN_KEY), verify=False)
    if backup.status_code == 200:
        backup_file = backup.headers['Content-Disposition'].split('; ')[1].split('=')[1].split('"')[1]
        backup_data = io.BytesIO(backup.content)
        logger.info('Backup Flynn cluster %s to %s/backup/%s' % (settings.AWS_ROUTE53_DOMAIN, settings.S3_BLOBSTORE, backup_file))
        return s3.upload_fileobj(backup_data, s3_bucket, 'backup/%s' % backup_file)


def get_latest_backup(s3_bucket):
    return sorted(s3_res.Bucket(s3_bucket).objects.all().filter(Prefix='backup/'), key=lambda k: k.last_modified, reverse=True)[0].key

