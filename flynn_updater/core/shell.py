import subprocess
import json
from django.conf import settings
from celery.utils.log import logger


def execute(cmd, shell=True):
    run = None
    try:
        run = subprocess.Popen(cmd, shell=shell, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        output = run.stdout.read().rstrip().split("\n")
    except subprocess.CalledProcessError:
        error = run.stderr.read().rstrip().split("\n")
        logger.error(error)
        return error
    return output


def flynn_cli_init():
    if not execute('ls %s' % settings.FLYNN_PATH)[0]:
        execute(settings.FLYNN_CLI_INSTALL)
        execute(settings.FLYNN_CLI_SETUP)


def flynn_cli_update():
    return execute('%s update' % settings.FLYNN_PATH)


def get_apps():
    return execute('%s apps | grep -v NAME | awk \'{print $2}\'' % settings.FLYNN_PATH)


def get_app_id(app):
    return execute('%s apps | grep %s | awk \'{print $1}\'' % (settings.FLYNN_PATH, app))[0]


def get_non_system_apps():
    apps = []
    for app in get_apps():
        if not int(execute('%s -a %s meta | grep flynn-system-app | grep -c true' % (settings.FLYNN_PATH, app))[0]):
            apps.append(app)
    return apps


def get_app_release(app):
    return execute('%s -a %s release -q' % (settings.FLYNN_PATH, app))


def get_app_current_release(app):
    return json.loads(execute('%s -a %s release show --json' % (settings.FLYNN_PATH, app))[0])['id']


def delete_app_release(app, release):
    return execute('%s -a %s release delete -y %s' % (settings.FLYNN_PATH, app, release))


def get_app_env(app):
    return execute('%s -a %s env' % (settings.FLYNN_PATH, app))


def get_app_release_json(app, id=''):
    return json.loads(execute('%s -a %s release show --json %s' % (settings.FLYNN_PATH, app, id))[0])


def update_app_release(app, release: json, id='', clean=False):
    file_path = '/tmp/%s-release.json' % app
    file_cmd = 'echo \'%s\' > %s' % (json.dumps(release), file_path)
    execute(file_cmd)
    return execute('%s -a %s release update %s %s' % (settings.FLYNN_PATH, app, file_path, id))


def set_app_env(app, envs: list):
    envars = ' \\'.join(envs)
    return execute('%s -a %s env set %s' % (settings.FLYNN_PATH, app, envars))
