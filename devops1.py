import os
import json
import time
import boto3
from botocore.exceptions import ClientError

from utils.dotenv import load_dotenv

REGION = 'us-east-1'
SECURITY_GROUP_NAME_PREFIX = 'devops1-sg'


load_dotenv()
KEY_NAME = os.environ.get('KEY_NAME', 'devops1-key')
PEM_FILE = os.environ.get('PEM_FILE', f'{KEY_NAME}.pem')
STATE_FILE = os.environ.get('DEVOPS_STATE_FILE', 'devops-state.json')
USERDATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'userdata.sh')

ec2_client = boto3.client('ec2', region_name=REGION)
ec2 = boto3.resource('ec2', region_name=REGION)


def get_default_vpc_id():
    """Return the default VPC ID for the region"""
    vpcs = ec2_client.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])
    if not vpcs['Vpcs']:
        raise RuntimeError(f'No default VPC found in {REGION}')
    return vpcs['Vpcs'][0]['VpcId']


def create_key_pair():
    """Use local .pem if PEM_FILE and .pub exist (import to AWS), else create in AWS and save"""
    pub_path = PEM_FILE + '.pub'
    if os.path.isfile(PEM_FILE) and os.path.isfile(pub_path):
        with open(pub_path, 'rb') as f:
            public_key = f.read()
        try:
            ec2_client.import_key_pair(KeyName=KEY_NAME, PublicKeyMaterial=public_key)
        except ClientError as e:
            if e.response['Error']['Code'] != 'InvalidKeyPair.Duplicate':
                raise
        return KEY_NAME
    response = ec2_client.create_key_pair(KeyName=KEY_NAME)
    with open(PEM_FILE, 'w') as f:
        f.write(response['KeyMaterial'])
    os.chmod(PEM_FILE, 0o600)
    return KEY_NAME


def create_security_group(vpc_id):
    """Create a new security group with SSH (22) and HTTP (80) from 0.0.0.0/0"""
    name = f'{SECURITY_GROUP_NAME_PREFIX}-{int(time.time())}'
    sg = ec2_client.create_security_group(
        GroupName=name,
        Description='SSH and HTTP for devops1 web server',
        VpcId=vpc_id,
    )
    sg_id = sg['GroupId']
    for port, desc in [(22, 'SSH'), (80, 'HTTP')]:
        ec2_client.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[{
                'FromPort': port,
                'ToPort': port,
                'IpProtocol': 'tcp',
                'IpRanges': [{'CidrIp': '0.0.0.0/0', 'Description': desc}],
            }],
        )
    return sg_id


def launch_instance(key_name, security_group_id):
    """Launch EC2 instance with the given key pair and security group"""
    if not os.path.isfile(USERDATA_FILE):
        raise FileNotFoundError(f'UserData script not found: {USERDATA_FILE}')
    with open(USERDATA_FILE, 'rb') as f:
        user_data = f.read()
    print(f'Using userdata: {USERDATA_FILE} ({len(user_data)} bytes)')
    try:
        instances = ec2.create_instances(
            ImageId='ami-0f3caa1cf4417e51b',
            MinCount=1,
            MaxCount=1,
            InstanceType='t2.nano',
            KeyName=key_name,
            SecurityGroupIds=[security_group_id],
            Placement={'AvailabilityZone': 'us-east-1b'},
            UserData=user_data,
        )
    except ClientError as e:
        raise RuntimeError(f'Failed to launch instance: {e}') from e
    return instances[0]


def _state_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), STATE_FILE)


def _read_state():
    path = _state_path()
    if not os.path.isfile(path):
        return {'instances': []}
    with open(path) as f:
        return json.load(f)


def _write_state(state):
    with open(_state_path(), 'w') as f:
        json.dump(state, f, indent=2)


def add_instance_to_state(instance_id):
    """Append a created instance ID to the state file for teardown."""
    state = _read_state()
    state.setdefault('instances', [])
    if instance_id not in state['instances']:
        state['instances'].append(instance_id)
    _write_state(state)


def main():
    try:
        print('Getting default VPC...')
        vpc_id = get_default_vpc_id()
        print('Setting up key pair...')
        key_name = create_key_pair()
        print('Creating security group...')
        sg_id = create_security_group(vpc_id)
        print('Launching instance (userdata will run on boot)...')
        instance = launch_instance(key_name, sg_id)
        add_instance_to_state(instance.id)
        print(f'Launched instance: {instance.id}')
        print(f'Key pair saved to: {PEM_FILE}')
        print(f'Security group: {sg_id}')
    except FileNotFoundError as e:
        print(f'Error: {e}')
        raise
    except ClientError as e:
        print(f'AWS error: {e.response["Error"].get("Code", "Unknown")} - {e.response["Error"].get("Message", str(e))}')
        raise
    except Exception as e:
        print(f'Error: {e}')
        raise


if __name__ == '__main__':
    main()
