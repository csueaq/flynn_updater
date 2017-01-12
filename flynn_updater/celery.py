from __future__ import absolute_import, unicode_literals

import os
from celery import Celery
from celery.schedules import crontab
from celery.utils.log import get_task_logger

from django.conf import settings
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
    install = 'L=/usr/local/bin/flynn && curl -sSL -A "`uname -sp`" https://dl.flynn.io/cli | zcat >$L && chmod +x $L'
    setup = 'flynn cluster add -p %s default %s %s' % (
        settings.FLYNN_PIN, settings.AWS_ROUTE53_DOMAIN, setttings.FLYNN_PIN)

    execute(install)
    execute(setup)

    apps = get_apps()
    addrs = get_instance_private_addr(get_instances(settings.AWS_AUTOSCALING_GROUP))

    for app in apps:
        releases = get_app_release(app)
        current = get_app_current_relase(app)
        for release in releases:
            if not release == current:
                delete_app_release(app, release)
                logger.info('Release deleted: %s (%s)' % (app, release))

    for host in addrs:
        logger.info('Volume cleanup: %s' % host)
        ssh_connect(host, settings.SSH_USER, settings.SSH_KEY)
        ssh_execute("sudo flynn-host volume gc")
        ssh_close()
