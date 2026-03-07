#!/usr/bin/env python3
"""
Teardown resources recorded in DEVOPS_STATE_FILE.
Terminates each instance and removes it from the state file only on success.
"""
import os
import json
import boto3
from botocore.exceptions import ClientError

def load_dotenv(path=None):
    """Load KEY=VALUE from .env into os.environ."""
    if path is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, '.env')
    if not os.path.isfile(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())

REGION = os.environ.get('AWS_REGION', 'us-east-1')


load_dotenv()
STATE_FILE = os.environ.get('DEVOPS_STATE_FILE', 'devops-state.json')


def _state_path():
    """Return absolute path to the state JSON file"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), STATE_FILE)


def _read_state():
    """Load state from JSON file; return dict with instances list if missing"""
    path = _state_path()
    if not os.path.isfile(path):
        return {'instances': []}
    with open(path) as f:
        return json.load(f)


def _write_state(state):
    """Write state dict to the state JSON file"""
    with open(_state_path(), 'w') as f:
        json.dump(state, f, indent=2)


def main():
    """Terminate each instance in state file and remove from file on success"""
    ec2_client = boto3.client('ec2', region_name=REGION)
    state = _read_state()
    instances = state.get('instances', [])

    if not instances:
        print('No instances in state file.')
        return

    for instance_id in list(instances):
        try:
            ec2_client.terminate_instances(InstanceIds=[instance_id])
            print(f'Terminated {instance_id}')
            state['instances'].remove(instance_id)
            _write_state(state)
        except ClientError as e:
            print(f'Failed to terminate {instance_id}: {e}')

    print('Teardown complete.')


if __name__ == '__main__':
    main()
