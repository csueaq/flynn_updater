import subprocess
import json


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


def get_apps():
    return execute("flynn apps | grep -v NAME | awk '{print $2}'")


def get_app_release(app):
    return execute("flynn -a %s release -q" % app)


def get_app_current_release(app):
    return json.loads(execute("flynn -a app release show --json" % app)[0])['id']


def delete_app_release(app, release):
    return execute("flynn -a %s release delete -y %s" % (app, release))
