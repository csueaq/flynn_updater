import subprocess
import json
from django.conf import settings
from celery.utils.log import logger


def execute(cmd, shell=True):
    run = None
    try:
        run = subprocess.Popen(cmd, shell=shell, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True)
        output = run.stdout.read().rstrip().split("\n")
    except subprocess.CalledProcessError:
        error = run.stderr.read().rstrip().split("\n")
        logger.error(error)
        return error
    return output


def flynn_cli_init():
    install = 'L=/usr/local/bin/flynn && curl -sSL -A "`uname -sp`" https://dl.flynn.io/cli | zcat >$L && chmod +x $L'
    setup = 'flynn cluster add -p %s default %s %s' % (
        settings.FLYNN_PIN, settings.AWS_ROUTE53_DOMAIN, settings.FLYNN_PIN)
    if not execute('which flynn')[0]:
        execute(install)
        execute(setup)


def flynn_cli_update():
    execute('flynn update')


def get_apps():
    return execute('flynn apps | grep -v NAME | awk \'{print $2}\'')


def get_app_release(app):
    return execute('flynn -a %s release -q' % app)


def get_app_current_release(app):
    return json.loads(execute('flynn -a app release show --json' % app)[0])['id']


def delete_app_release(app, release):
    return execute('flynn -a %s release delete -y %s' % (app, release))


def get_app_env(app):
    return execute('flynn -a %s env' % app)


def set_app_env(app, envs: list):
    envars = ' \\'.join(envs)
    return execute('flynn -a %s env set %s' % (app, envars))
