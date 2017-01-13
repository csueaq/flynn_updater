import paramiko
from celery.utils.log import logger

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy)


def ssh_connect(host, user, key):
    try:
        ssh.connect(host, username=user, pkey=key)
    except Exception as error:
        logger.error(error)


def ssh_execute(command):
    stdout, stderr = None
    try:
        stdout, stderr = ssh.exec_command(command)
    except Exception as error:
        logger.error(error)
        logger.error(stderr)
        return stderr
    return stdout


def ssh_close():
    ssh.close()
