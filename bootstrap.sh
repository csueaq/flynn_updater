#!/bin/bash -xe

export AWS_DEFAULT_REGION=$(curl -Lfs http://169.254.169.254/latest/dynamic/instance-identity/document|grep region|awk -F\" '{print $4}')
INSTANCE_IP=$(curl -Lfs http://169.254.169.254/latest/meta-data/local-ipv4)
DEFAULT_WAIT=60
BOOTSTRAP_RETRY=0
MAX_BOOTSTRAP_RETRY=5

ASG_NODES=null
DISCOVERY_NODES=null
ASG_NODES_COUNT=null
CLUSTER_NODES=null
CLUSTER_NODES_COUNT=null
DISCOVERY_NODES_COUNT=null

function WAIT() {
  sleep $DEFAULT_WAIT
}

function GET_ASG_NODES() {
  ASG_NODES=$(aws ec2 describe-instances --instance-ids "`aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names ${flynn_domain} | jq -r '[.AutoScalingGroups[].Instances[].InstanceId]'`" --filters Name=instance-state-name,Values=running | jq -c '.Reservations[].Instances[].InstanceId')
}

function GET_ASG_NODES_IP() {
  ASG_NODES=$(aws ec2 describe-instances --instance-ids "`aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names ${flynn_domain} | jq -r '[.AutoScalingGroups[].Instances[].InstanceId]'`" --filters Name=instance-state-name,Values=running | jq -c '.Reservations[].Instances[].PrivateIpAddress')
}

function GET_ASG_NODES_COUNT() {
  ASG_NODES_COUNT=$(aws ec2 describe-instances --instance-ids "`aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names ${flynn_domain} | jq -r '[.AutoScalingGroups[].Instances[].InstanceId]'`" --filters Name=instance-state-name,Values=running | jq '[.Reservations[].Instances[].InstanceId] | length')
}

function GET_DISCOVERY_NODES() {
  DISCOVERY_NODES=$(curl -Lfs https://discovery.flynn.io/clusters/${discovery_token}/instances | jq -c '.data | sort_by(.created_at)[].url' | sed -E 's/(https?:\/\/|:[0-9]+)//g')
}

function GET_DISCOVERY_NODES_COUNT() {
  DISCOVERY_NODES_COUNT=$(curl -Lfs https://discovery.flynn.io/clusters/${discovery_token}/instances | jq -rc '[.data[].url] | length')
}

function GET_CLUSTER_NODES() {
  CLUSTER_NODES=$(flynn-host list | grep -E '[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}')
}

function GET_CLUSTER_NODES_COUNT() {
  CLUSTER_NODES_COUNT=$(flynn-host list | grep -c -E '[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}')
}

function UPDATE_ROUTE53_RECORD() {
  ROUTE53_RECORD=$(aws ec2 describe-instances --instance-ids "`aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names ${flynn_domain} | jq -r '[.AutoScalingGroups[].Instances[].InstanceId]'`" --filters Name=instance-state-name,Values=running | jq '[.Reservations[].Instances[] | {Value: .PublicIpAddress}]') &&
  echo '{
    "Comment": "auto updated @ '$(date)'",
    "Changes": [{
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "${flynn_domain}",
        "Type": "A",
        "TTL": 60,
        "ResourceRecords": '$ROUTE53_RECORD'
      }
    }]
  }' > /tmp/route53_update.json
  aws route53 change-resource-record-sets --hosted-zone-id ${dns_zone_id} --change-batch file:///tmp/route53_update.json
}

function FLYNN_BOOTSTRAP() {
  GET_ASG_NODES_IP
  PEERS=$(echo $ASG_NODES | sed -e 's/"//g' | tr '\ ' ',')
  CLUSTER_DOMAIN="${flynn_domain}" flynn-host bootstrap -t 3600 --min-hosts ${flynn_nodes} --discovery https://discovery.flynn.io/clusters/${discovery_token} --peer-ips=$PEERS
}

function FLYNN_RESTORE() {
  BACKUP=$(aws s3 ls s3://${flynn_s3}/backup/ | awk '{print $4}' | sort -r | head -n 1)
  aws s3 cp "s3://${flynn_s3}/backup/$BACKUP" "/tmp/$BACKUP"
  GET_ASG_NODES_IP
  PEERS=$(echo $ASG_NODES | sed -e 's/"//g' | tr '\ ' ',')
  flynn-host bootstrap -t 3600 --min-hosts ${flynn_nodes} --from-backup="/tmp/$BACKUP" --peer-ips=$PEERS
  return $?
}

function FLYNN_PROMOTE_NODE() {
  flynn-host promote $INSTANCE_IP
}

function IS_NODE_READY() {
  if [[ "$(flynn-host list | grep "$INSTANCE_IP" | awk '{print $3}')" == "proxy" ]]; then
    return 0
  else
    return 1
  fi
}

function IS_BOOTSTRAP_NODE() {
  if [[ "$INSTANCE_IP" == "$(curl -Lfs https://discovery.flynn.io/clusters/${discovery_token}/instances | jq -rc '.data | sort_by(.created_at) [0].url' | sed -E 's/(https?:\/\/|:[0-9]+)//g')" ]]; then
    echo 1
  else
    echo 0
  fi
}

function IS_CLUSTER_READY() {
  if [[ $(flynn-host discover --json flynn-host | jq '.[].Instances | length') -lt ${flynn_nodes} ]] || [[ $(flynn-host discover --json discoverd | jq '.[].Instances | length') -lt ${flynn_nodes} ]] || [[ $(flynn-host discover --json postgres | jq '.[].Instances | length') -lt 3 ]]; then
    echo 0
  else
    echo 1
  fi
}

function IS_RESTORE_NODE() {
  GET_ASG_NODES_IP
  GET_DISCOVERY_NODES
  FIRST=1
  N=$(curl -Lfs https://discovery.flynn.io/clusters/${discovery_token}/instances | jq '.data | [sort_by(.created_at)[].url] | index("http://'$INSTANCE_IP':1113")')
  for X in $ASG_NODES; do
    I=0
    for Y in $DISCOVERY_NODES; do
      if [[ "$X" == "$Y" ]]; then
        if [[ $I -lt $N ]]; then
          FIRST=0
        fi
      fi
      I=$(( $I + 1 ))
    done
  done
  echo $FIRST
}

function main() {
  while [[ $ASG_NODES_COUNT -lt ${flynn_nodes} ]] || [[ $DISCOVERY_NODES_COUNT -lt ${flynn_nodes} ]]; do
    WAIT
    GET_ASG_NODES_COUNT
    GET_DISCOVERY_NODES_COUNT
  done

  UPDATE_ROUTE53_RECORD

  IS_BOOTSTRAP_NODE=$(IS_BOOTSTRAP_NODE)
  if [[ $IS_BOOTSTRAP_NODE -eq 1 ]]; then
    FLYNN_BOOTSTRAP
  else
    while [[ $BOOTSTRAP_RETRY -lt $MAX_BOOTSTRAP_RETRY ]]; do
      IS_CLUSTER_READY=$(IS_CLUSTER_READY)
      if [[ $IS_CLUSTER_READY -eq 1 ]]; then
        BOOTSTRAP_RETRY=255
      else
        BOOTSTRAP_RETRY=$(( $BOOTSTRAP_RETRY + 1 ))
        WAIT
      fi
    done
    if [[ $BOOTSTRAP_RETRY -eq $MAX_BOOTSTRAP_RETRY ]]; then
      IS_RESTORE_NODE=$(IS_RESTORE_NODE)
      if [[ $IS_RESTORE_NODE -eq 1 ]]; then
        until FLYNN_RESTORE; do
          DEFAULT_WAIT=300
          WAIT
        done
      else
        until IS_NODE_READY; do
          WAIT
        done
        FLYNN_PROMOTE_NODE
      fi
    fi
  fi
}

main
