"""
devops1.py – Assignment 1: EC2 web server + S3 static website.
Parts 1–4: EC2 launch (key, SG, userdata), S3 bucket (kcolville-XXXXXX) with logo + index, website hosting.
"""
import json
import os
import random
import string
import urllib.request

import boto3
from botocore.exceptions import ClientError

# -----------------------------------------------------------------------------
# Config: load .env then constants
# -----------------------------------------------------------------------------


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


load_dotenv()

REGION = 'us-east-1'
SECURITY_GROUP_NAME_PREFIX = 'devops1-sg'
KEY_NAME = os.environ.get('KEY_NAME', 'devops1-key')
PEM_FILE = os.environ.get('PEM_FILE', f'{KEY_NAME}.pem')
STATE_FILE = os.environ.get('DEVOPS_STATE_FILE', 'devops-state.json')
USERDATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'userdata.sh')

# S3 website (part 4): bucket name = kcolville + 6 random chars
S3_BUCKET_PREFIX = 'kcolville'
LOGO_URL = 'http://devops.setudemo.net/logo.jpg'
WEBSITES_FILENAME = 'kcolville-websites.txt'

# -----------------------------------------------------------------------------
# AWS clients
# -----------------------------------------------------------------------------

ec2_client = boto3.client('ec2', region_name=REGION)
ec2 = boto3.resource('ec2', region_name=REGION)
s3_client = boto3.client('s3', region_name=REGION)

# -----------------------------------------------------------------------------
# State file helpers
# -----------------------------------------------------------------------------


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


# -----------------------------------------------------------------------------
# EC2: VPC, key pair, security group, launch
# -----------------------------------------------------------------------------


def get_default_vpc_id():
    """Return the default VPC ID for the region."""
    vpcs = ec2_client.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])
    if not vpcs['Vpcs']:
        raise RuntimeError(f'No default VPC found in {REGION}')
    return vpcs['Vpcs'][0]['VpcId']


def create_key_pair():
    """Use local .pem if PEM_FILE and .pub exist (import to AWS), else create in AWS and save."""
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
    """Create security group with SSH (22) and HTTP (80) from 0.0.0.0/0.
    Uses iterated name (devops1-sg-1, devops1-sg-2, ...)."""
    state = _read_state()
    next_index = state.get('next_sg_index', 1)
    name = f'{SECURITY_GROUP_NAME_PREFIX}-{next_index}'
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
    state['next_sg_index'] = next_index + 1
    state.setdefault('security_groups', []).append(sg_id)
    _write_state(state)
    return sg_id


def launch_instance(key_name, security_group_id):
    """Launch EC2 instance with the given key pair and security group."""
    if not os.path.isfile(USERDATA_FILE):
        raise FileNotFoundError(f'UserData script not found: {USERDATA_FILE}')
    with open(USERDATA_FILE, 'rb') as f:
        user_data = f.read()
    print(f'Using userdata: {USERDATA_FILE} ({len(user_data)} bytes)')
    try:
        instances = ec2.create_instances(
            ImageId='ami-02dfbd4ff395f2a1b',
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


def add_instance_to_state(instance_id):
    """Append a created instance ID to the state file for teardown."""
    state = _read_state()
    state.setdefault('instances', [])
    if instance_id not in state['instances']:
        state['instances'].append(instance_id)
    _write_state(state)


# -----------------------------------------------------------------------------
# S3 website: bucket, logo, index.html, static hosting (part 4)
# -----------------------------------------------------------------------------


def _random_bucket_suffix(length=6):
    """Return a string of random lowercase letters and digits."""
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))


def create_s3_bucket_name():
    """Bucket name: kcolville-XXXXXX (globally unique)."""
    return f'{S3_BUCKET_PREFIX}-{_random_bucket_suffix()}'


def create_s3_bucket(bucket_name):
    """Create S3 bucket in REGION. Idempotent if bucket already exists."""
    try:
        s3_client.create_bucket(
            Bucket=bucket_name,
            CreateBucketConfiguration={'LocationConstraint': REGION} if REGION != 'us-east-1' else {},
        )
    except ClientError as e:
        if e.response['Error']['Code'] != 'BucketAlreadyOwnedByYou':
            raise
    return bucket_name


def download_logo():
    """Download logo from LOGO_URL; handle URL changes generically."""
    req = urllib.request.Request(LOGO_URL, headers={'User-Agent': 'devops1.py'})
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def upload_logo_to_bucket(bucket_name, data):
    """Upload logo as logo.jpg with correct Content-Type."""
    s3_client.put_object(
        Bucket=bucket_name,
        Key='logo.jpg',
        Body=data,
        ContentType='image/jpeg',
    )


def create_index_html():
    """Return HTML string that displays the logo (image at logo.jpg)."""
    return '''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>S3 Website</title></head>
<body>
<h1>DevOps Assignment 1 - S3 static website</h1>
<p><img src="logo.jpg" alt="Logo" /></p>
</body>
</html>
'''


def upload_index_to_bucket(bucket_name, html_content):
    """Upload index.html for website default document."""
    s3_client.put_object(
        Bucket=bucket_name,
        Key='index.html',
        Body=html_content.encode('utf-8'),
        ContentType='text/html',
    )


def enable_s3_website_hosting(bucket_name):
    """Configure bucket for static website hosting (index document: index.html)."""
    s3_client.put_bucket_website(
        Bucket=bucket_name,
        WebsiteConfiguration={
            'IndexDocument': {'Suffix': 'index.html'},
        },
    )


def set_bucket_policy_public_read(bucket_name):
    """Allow public read via bucket policy so website endpoint can serve objects."""
    # New buckets block public access by default; allow policy to grant public read
    s3_client.put_public_access_block(
        Bucket=bucket_name,
        PublicAccessBlockConfiguration={
            'BlockPublicAcls': False,
            'IgnorePublicAcls': False,
            'BlockPublicPolicy': False,
            'RestrictPublicBuckets': False,
        },
    )
    policy = {
        'Version': '2012-10-17',
        'Statement': [
            {
                'Sid': 'PublicReadGetObject',
                'Effect': 'Allow',
                'Principal': '*',
                'Action': 's3:GetObject',
                'Resource': f'arn:aws:s3:::{bucket_name}/*',
            }
        ],
    }
    s3_client.put_bucket_policy(Bucket=bucket_name, Policy=json.dumps(policy))


def setup_s3_website():
    """
    Create S3 bucket (kcolville-XXXXXX), upload logo and index.html,
    enable static website hosting, set public read policy.
    Returns (bucket_name, website_url).
    """
    bucket_name = create_s3_bucket_name()
    create_s3_bucket(bucket_name)
    logo_data = download_logo()
    upload_logo_to_bucket(bucket_name, logo_data)
    upload_index_to_bucket(bucket_name, create_index_html())
    enable_s3_website_hosting(bucket_name)
    set_bucket_policy_public_read(bucket_name)
    # Website URL form: http://bucket-name.s3-website-us-east-1.amazonaws.com
    website_url = f'http://{bucket_name}.s3-website-{REGION}.amazonaws.com'
    return bucket_name, website_url


# -----------------------------------------------------------------------------
# Output: write URLs to file (part 5)
# -----------------------------------------------------------------------------


def write_websites_file(ec2_url, s3_website_url):
    """Write the two website URLs to kcolville-websites.txt."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), WEBSITES_FILENAME)
    with open(path, 'w') as f:
        f.write(f'{ec2_url}\n{s3_website_url}\n')
    print(f'Wrote URLs to {path}')


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def main():
    try:
        # EC2 (parts 1–3)
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

        # S3 website (part 4)
        print('Setting up S3 website...')
        bucket_name, s3_website_url = setup_s3_website()
        print(f'S3 bucket: {bucket_name}')
        print(f'S3 website URL: {s3_website_url}')

        # Wait for instance to get public IP then write URLs (part 5)
        print('Waiting for instance to be running...')
        instance.wait_until_running()
        instance.reload()
        ec2_url = f'http://{instance.public_ip_address}' if instance.public_ip_address else ''
        write_websites_file(ec2_url, s3_website_url)
    except FileNotFoundError as e:
        print(f'Error: {e}')
        raise
    except ClientError as e:
        err = e.response['Error']
        print(f'AWS error: {err.get("Code", "Unknown")} - {err.get("Message", str(e))}')
        raise
    except Exception as e:
        print(f'Error: {e}')
        raise


if __name__ == '__main__':
    main()
