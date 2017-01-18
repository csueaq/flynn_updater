from __future__ import absolute_import, unicode_literals

import os

from celery import Celery
from celery.schedules import crontab
from celery.utils.log import get_task_logger
from urllib.parse import urlparse

from flynn_updater.core.utils import *
from flynn_updater.core.shell import *
from flynn_updater.core.ssh import *

# set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'flynn_updater.settings')

worker = Celery('flynn_updater', backend=settings.REDIS_URL, broker=settings.REDIS_URL)

# Using a string here means the worker will not have to
# pickle the object when using Windows.

worker.config_from_object('django.conf:settings', namespace='CELERY')
worker.autodiscover_tasks(lambda: settings.INSTALLED_APPS)
logger = get_task_logger(__name__)

worker.conf.timezone = settings.TIMEZONE
worker.conf.beat_schedule = {
    'Flynn DNS update': {
        'task': 'flynn_dns_update',
        'schedule': 60.0,
        'args': ()
    },
    'Flynn garbage collection': {
        'task': 'flynn_gc',
        'schedule': crontab(hour=7, minute=30, day_of_week=6),
        'args': ()
    },
    'Flynn S3 datastore': {
        'task': 'flynn_s3_store',
        'schedule': 300.0,
        'args': ()
    },
    'Flynn CLI update': {
        'task': 'flynn_cli_update',
        'schedule': crontab(hour=0, minute=30, day_of_week='*'),
        'args': ()
    },
}


@worker.task(name='flynn_dns_update')
def flynn_dns_update():
    addrs = get_instance_public_addr(get_instances([settings.AWS_AUTOSCALING_GROUP]))
    logger.info(
        'DNS update: %s (%s) with record %s' % (settings.AWS_ROUTE53_DOMAIN, settings.AWS_ROUTE53_ZONE, addrs))
    record_set = []
    for addr in addrs:
        record_set.append({'Value': addr})
    dns_update(zone_id=settings.AWS_ROUTE53_ZONE, domain=settings.AWS_ROUTE53_DOMAIN, records=record_set)
    logger.info('DNS update complete.')


@worker.task(name='flynn_gc')
def flynn_gc():
    flynn_cli_init()
    apps = get_apps()
    addrs = get_instance_private_addr(get_instances(settings.AWS_AUTOSCALING_GROUP))

    for app in apps:
        releases = get_app_release(app)
        current = get_app_current_release(app)
        for release in releases:
            if not release == current:
                delete_app_release(app, release)
                logger.info('Release deleted: %s (%s)' % (app, release))

    for host in addrs:
        logger.info('Volume cleanup: %s' % host)
        ssh_connect(host, settings.SSH_USER, settings.SSH_KEY)
        ssh_execute("sudo flynn-host volume gc")
        ssh_close()


@worker.task(name='flynn_s3_store')
def flynn_s3_store():
    flynn_cli_init()
    blobstore = get_app_env('blobstore')
    s3_enabled = False
    for var in blobstore:
        if 'DEFAULT_BACKEND=s3main' in var:
            s3_enabled = True

    if not s3_enabled:
        s3_params = ['BACKEND_S3MAIN="backend=s3 region=%s bucket=%s ec2_role=true"'
                     % (settings.AWS_DEFAULT_REGION, settings.S3_BLOBSTORE), 'DEFAULT_BACKEND=s3main']
        set_app_env('blobstore', s3_params)
        execute('flynn -a blobstore run /bin/flynn-blobstore migrate --delete')


@worker.task(name='flynn_cli_update')
def flynn_cli_update():
    flynn_cli_update()


@worker.task(name='flynn_update_discovery_instances')
def flynn_update_discovery_instances():
    discovered_instances = get_discovery_instances(settings.FLYNN_DISCOVERY_TOKEN)
    asg_instances = get_instance_private_addr(get_instances([settings.AWS_AUTOSCALING_GROUP]))
    for addr in asg_instances:
        instance_exist = False
        instance_data = {'data': {}}
        for instance in discovered_instances['data']:
            if addr in urlparse(instance['url']).hostname:
                instance_exist = True
        if not instance_exist:
            instance_data['data']['name'] = addr
            instance_data['data']['url'] = urlparse('http://%s:1113' %addr).geturl()
            update_discovery_instances(settings.FLYNN_DISCOVERY_TOKEN, instance_data)
