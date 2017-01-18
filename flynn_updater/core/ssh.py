import paramiko
import io
from celery.utils.log import logger

ssh = paramiko.SSHClient()
ssh.load_system_host_keys()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())


def ssh_connect(host, user, key):
    private_key = paramiko.RSAKey.from_private_key(io.StringIO(key))
    try:
        ssh.connect(host, username=user, pkey=private_key)
    except Exception as error:
        logger.error(error)


def ssh_execute(command):
    stdin, stdout, stderr = None
    try:
        stdin, stdout, stderr = ssh.exec_command(command)
    except Exception as error:
        logger.error(error)
        logger.error(stderr.readlines())
        return [err.replace("\n", '') for err in stderr.readlines()]
    return [out.replace("\n", '') for out in stdout.readlines()]


def ssh_close():
    ssh.close()
