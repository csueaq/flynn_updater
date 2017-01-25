from __future__ import absolute_import, unicode_literals

import os

from celery import Celery
from celery.schedules import crontab
from celery.utils.log import get_task_logger
from urllib.parse import urlparse
from random import randint

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
    'Flynn discoverd update': {
        'task': 'flynn_update_discoverd_peers',
        'schedule': 600,
        'args': ()
    },
    'Flynn remote dead node': {
        'task': 'flynn_demote_dead_node',
        'schedule': 1800.0,
        'args': ()
    },
    'Flynn garbage collection': {
        'task': 'flynn_gc',
        'schedule': crontab(hour=6, minute=30, day_of_week=6),
        'args': ()
    },
    'Flynn log GC': {
        'task': 'flynn_log_gc',
        'schedule': crontab(hour=7, minute=30, day_of_week=6),
        'args': ()
    },
    'Flynn S3 datastore': {
        'task': 'flynn_s3_store',
        'schedule': 300.0,
        'args': ()
    },
    # 'Flynn RDS database': {
    #     'task': 'flynn_rds_db',
    #     'schedule': 300.0,
    #     'args': ()
    # },
    # 'Flynn RDS security group update': {
    #     'task': 'flynn_rds_security_group_update',
    #     'schedule': 60.0,
    #     'args': ()
    # },
    'Flynn CLI update': {
        'task': 'flynn_cli_update',
        'schedule': crontab(hour=0, minute=30, day_of_week='*'),
        'args': ()
    },
}


@worker.task(name='flynn_dns_update')
def flynn_dns_update():
    asg_instances = get_instances([settings.AWS_AUTOSCALING_GROUP])
    running_instances = get_instances_by_state(asg_instances)
    addrs = get_instance_public_addr(running_instances)
    logger.info('DNS update: %s (%s) with record %s' % (settings.AWS_ROUTE53_DOMAIN, settings.AWS_ROUTE53_ZONE, addrs))
    record_set = []
    for addr in addrs:
        record_set.append({'Value': addr})
    dns_update(zone_id=settings.AWS_ROUTE53_ZONE, domain=settings.AWS_ROUTE53_DOMAIN, records=record_set)
    logger.info('DNS update complete.')


@worker.task(name='flynn_gc')
def flynn_gc():
    flynn_cli_init()
    apps = get_apps()
    asg_instances = get_instances([settings.AWS_AUTOSCALING_GROUP])
    running_instances = get_instances_by_state(asg_instances)
    addrs = get_instance_private_addr(running_instances)

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
        stdout, stder = ssh_execute('sudo flynn-host volume gc')
        logger.info('Volume deleted: %s' % stdout)
        logger.error('Volume delete error: %s' % stder)
        ssh_close()


@worker.task(name='flynn_demote_dead_node')
def flynn_demote_dead_node():
    asg_instances = get_instances([settings.AWS_AUTOSCALING_GROUP])
    running_instances = get_instances_by_state(asg_instances)
    addrs = get_instance_private_addr(running_instances)
    dead_instances = get_instances_by_state(asg_instances, 'terminated')
    for dead_instance in dead_instances:
        logger.info('Dead node detected: %s' % dead_instance['InstanceId'])
        ssh_connect(addrs[randint(0, len(addrs) - 1)], settings.SSH_USER, settings.SSH_KEY)
        ssh_execute("sudo flynn-host demote --force %s" % get_instance_private_addr([dead_instance])[0])
        logger.info('Dead node removed: %s' % dead_instance['InstanceId'])
        ssh_close()


@worker.task(name='flynn_s3_store')
def flynn_s3_store():
    flynn_cli_init()
    blobstore = get_app_env('blobstore')
    s3_enabled = False
    for var in blobstore:
        if 'DEFAULT_BACKEND=s3main' in var:
            s3_enabled = True
            logger.info('S3 blobstore is not enabled.')

    if not s3_enabled:
        s3_params = ['BACKEND_S3MAIN="backend=s3 region=%s bucket=%s ec2_role=true"'
                     % (settings.AWS_DEFAULT_REGION, settings.S3_BLOBSTORE), 'DEFAULT_BACKEND=s3main']
        logger.info('S3 blobstore is configure to use S3 bucket %s in %s.' % (settings.S3_BLOBSTORE, settings.AWS_DEFAULT_REGION))
        set_app_env('blobstore', s3_params)
        logger.info('Migrating local blobstore to S3 bucket %s' % settings.S3_BLOBSTORE)
        execute('%s -a blobstore run /bin/flynn-blobstore migrate --delete' % settings.FLYNN_PATH)


@worker.task(name='flynn_cli_update')
def flynn_cli_update():
    logger.info('Flynn-CLI upgrade.')
    flynn_cli_update()


@worker.task(name='flynn_update_discoverd_peers')
def flynn_update_discoverd_peers():
    flynn_cli_init()
    discoverd = get_app_release_json('discoverd')
    exist_peers = discoverd['env']['DISCOVERD_PEERS'].replace(':1111', '').split(',')
    asg_instances = get_instances([settings.AWS_AUTOSCALING_GROUP])
    running_instances = get_instances_by_state(asg_instances)
    addrs = get_instance_private_addr(running_instances)
    update_peers = []
    update_required = False
    for I in addrs:
        update_peers.append(I + ':1111')
        if I not in exist_peers:
            logger.info('discoverd found new node: %s' % I)
            update_required = True
    if update_required:
        discoverd['env']['DISCOVERD_PEERS'] = ','.join(update_peers)
        update_app_release('discoverd', discoverd)
        logger.info('discoverd updated with %s' % update_peers)


@worker.task(name='flynn_rds_db')
def flynn_rds_db():
    apps = ['blobstore', 'router', 'controller']
    asg_instances = get_instances([settings.AWS_AUTOSCALING_GROUP])
    running_instances = get_instances_by_state(asg_instances)
    addrs = get_instance_public_addr(running_instances)
    rd_endpoint = get_rds_endpoint(settings.RDS_DB_ID)
    flynn_cli_init()
    for app in apps:
        app_pg_host = 'PGHOST=%s' % rd_endpoint
        app_pg_database = 'PGDATABASE=%s' % app
        app_pg_user = 'PGUSER=%s' % settings.DB_USER
        app_pg_password = 'PGPASSWORD=%s' % settings.DB_PASSWORD
        app_database_url = 'DATABASE_URL=postgres://%s:%s@%s:%s/%s%s' % (settings.DB_USER, settings.DB_PASSWORD, rd_endpoint, settings.DB_PORT, app, settings.DB_OPTS)
        app_env = get_app_env(app)
        if app_pg_host not in app_env or app_pg_database not in app_env or app_pg_user not in app_env or app_pg_password not in app_env or app_database_url not in app_env:
            logger.info('Flynn %s is not configured to use RDS.' % app)
            ssh_connect(addrs[randint(0, len(addrs) - 1)], settings.SSH_USER, settings.SSH_KEY)
            logger.info('Preparing Flynn %s RDS update.' % app)
            ssh_execute('flynn cluster add -p %s default %s %s' % (settings.FLYNN_PIN, settings.AWS_ROUTE53_DOMAIN, settings.FLYNN_KEY))
            ssh_execute('flynn -a %s pg dump -q -f %s.psql' % (app, app))
            logger.info('Create Flynn %s RDS database.' % app)
            ssh_execute('PGPASSWORD=%s psql -h %s -U %s -c "DROP DATABASE IF EXISTS %s"' % (settings.DB_PASSWORD, rd_endpoint, settings.DB_USER, app))
            ssh_execute('PGPASSWORD=%s psql -h %s -U %s -c "CREATE DATABASE %s OWNER=%s"' % (settings.DB_PASSWORD, rd_endpoint, settings.DB_USER, app, settings.DB_USER))
            logger.info('Migrate Flynn %s RDS database.' % app)
            ssh_execute('PGPASSWORD=%s pg_restore -h %s -U %s -d %s < %s.psql' % (settings.DB_PASSWORD, rd_endpoint, settings.DB_USER, app, app))
            ssh_execute('rm -f %s.psql' % app)
            ssh_execute('flynn cluster remove default')
            ssh_close()
            logger.info('Configuring Flynn %s to use RDS database (%s).' % (app, app_database_url))
            set_app_env(app, [app_pg_host, app_pg_database, app_pg_user, app_pg_password, app_database_url])


@worker.task(name='flynn_rds_security_group_update')
def flynn_rds_security_group_update():
    asg_instances = get_instances([settings.AWS_AUTOSCALING_GROUP])
    running_instances = get_instances_by_state(asg_instances)
    addrs = get_instance_public_addr(running_instances)
    dead_instances = get_instances_by_state(asg_instances, 'terminated')
    dead_addrs = get_instance_public_addr(dead_instances)
    dns_records = get_route53_records(zone_id=settings.AWS_ROUTE53_ZONE, domain=settings.AWS_ROUTE53_DOMAIN)
    rds_security_group = get_rds_securitygroup(settings.RDS_DB_ID)
    for addr in dead_addrs:
        logger.info('Removing dead node (%s) RDS access.' % addr)
        remove_security_group_rule(rds_security_group, addr, 5432)
    for addr in addrs:
        if addr not in dns_records:
            logger.info('Adding new node (%s) RDS access.' % addr)
        add_security_group_rule(rds_security_group, addr, 5432)


@worker.task(name='flynn_log_gc')
def flynn_log_gc():
    flynn_cli_init()
    asg_instances = get_instances([settings.AWS_AUTOSCALING_GROUP])
    running_instances = get_instances_by_state(asg_instances)
    addrs = get_instance_public_addr(running_instances)
    for addr in addrs:
        logger.info('Clean up log on %s' %addr)
        ssh_connect(addr, settings.SSH_USER, settings.SSH_KEY)
        ssh_execute('sudo find /var/log/flynn -mtime +7 -iname *.log ! -iname flynn-host.log -delete')
        ssh_close()
    for app in get_apps():
        app_id = get_app_id(app)
        logger.info('Clean up %s (%s) logs' % (app, app_id))
        execute("%s -a controller pg psql -- -c \" delete from job_cache where app_id='%s' and state!='up' andcreated_at < now() - interval '7 days'\"" % (settings.FLYNN_PATH, app_id))

