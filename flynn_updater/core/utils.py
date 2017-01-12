import datetime
import boto3

asg = boto3.client('autoscaling')
ec2 = boto3.resource('ec2')
dns = boto3.client('route53')


def get_instances(asg_id: list):
    asg_instances = asg.describe_auto_scaling_groups(AutoScalingGroupNames=asg_id)['AutoScalingGroups'][0]['Instances']
    return asg_instances


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
