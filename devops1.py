import os
import json
import boto3
from botocore.exceptions import ClientError

from utils.dotenv import load_dotenv

REGION = 'us-east-1'
SECURITY_GROUP_NAME = 'devops1-sg'


load_dotenv()
KEY_NAME = os.environ.get('KEY_NAME', 'devops1-key')
PEM_FILE = os.environ.get('PEM_FILE', f'{KEY_NAME}.pem')
STATE_FILE = os.environ.get('DEVOPS_STATE_FILE', 'devops-state.json')

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
    """Create a security group allowing SSH (22) and HTTP (80), or return existing one."""
    sgs = ec2_client.describe_security_groups(
        Filters=[
            {'Name': 'vpc-id', 'Values': [vpc_id]},
            {'Name': 'group-name', 'Values': [SECURITY_GROUP_NAME]},
        ],
    )
    if sgs['SecurityGroups']:
        return sgs['SecurityGroups'][0]['GroupId']
    sg = ec2_client.create_security_group(
        GroupName=SECURITY_GROUP_NAME,
        Description='SSH and HTTP for devops1 web server',
        VpcId=vpc_id,
    )
    sg_id = sg['GroupId']
    ec2_client.authorize_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=[
            {'FromPort': 22, 'ToPort': 22, 'IpProtocol': 'tcp', 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]},
            {'FromPort': 80, 'ToPort': 80, 'IpProtocol': 'tcp', 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]},
        ],
    )
    return sg_id


def launch_instance(key_name, security_group_id):
    """Launch EC2 instance with the given key pair and security group"""
    with open('userdata.sh', 'rb') as f:
        user_data = f.read()
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
    vpc_id = get_default_vpc_id()
    key_name = create_key_pair()
    sg_id = create_security_group(vpc_id)
    instance = launch_instance(key_name, sg_id)
    add_instance_to_state(instance.id)
    print(f'Launched instance: {instance.id}')
    print(f'Key pair saved to: {PEM_FILE}')
    print(f'Security group: {sg_id}')


if __name__ == '__main__':
    main()
