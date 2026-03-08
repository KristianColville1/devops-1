"""
devops1.py - Assignment 1: EC2 web server + S3 static website.
Parts 1-4: EC2 launch (key, SG, userdata), S3 bucket (kcolville-XXXXXX) with logo + index, website hosting.
"""

import json
import os
import random
import string
import sys
from datetime import datetime, timezone, timedelta

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
        path = os.path.join(root, ".env")
    if not os.path.isfile(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


load_dotenv()

REGION = "us-east-1"
SECURITY_GROUP_NAME_PREFIX = "devops1-sg"
KEY_NAME = os.environ.get("KEY_NAME", "devops1-key")
PEM_FILE = os.environ.get("PEM_FILE", f"{KEY_NAME}.pem")
STATE_FILE = os.environ.get("DEVOPS_STATE_FILE", "devops-state.json")
USERDATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "userdata.sh")

# S3 website (part 4): bucket name = kcolville + 6 random chars
S3_BUCKET_PREFIX = "kcolville"
LOGO_URL = "http://devops.setudemo.net/logo.jpg"
WEBSITES_FILENAME = "kcolville-websites.txt"

# AMI creation: name format XX-YYYY-MM-DD-microseconds (XX = initials)
AMI_NAME_PREFIX = "kc"

# -----------------------------------------------------------------------------
# AWS clients
# -----------------------------------------------------------------------------

ec2_client = boto3.client("ec2", region_name=REGION)
ec2 = boto3.resource("ec2", region_name=REGION)
s3_client = boto3.client("s3", region_name=REGION)
cloudwatch_client = boto3.client("cloudwatch", region_name=REGION)

# -----------------------------------------------------------------------------
# State file helpers
# -----------------------------------------------------------------------------


def _state_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), STATE_FILE)


def _read_state():
    path = _state_path()
    if not os.path.isfile(path):
        return {"instances": []}
    with open(path) as f:
        return json.load(f)


def _write_state(state):
    with open(_state_path(), "w") as f:
        json.dump(state, f, indent=2)


# -----------------------------------------------------------------------------
# EC2: VPC, key pair, security group, launch
# -----------------------------------------------------------------------------


def get_default_vpc_id():
    """Return the default VPC ID for the region."""
    vpcs = ec2_client.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
    if not vpcs["Vpcs"]:
        raise RuntimeError(f"No default VPC found in {REGION}")
    return vpcs["Vpcs"][0]["VpcId"]


def create_key_pair():
    """Use local .pem if PEM_FILE and .pub exist (import to AWS), else create in AWS and save."""
    pub_path = PEM_FILE + ".pub"
    if os.path.isfile(PEM_FILE) and os.path.isfile(pub_path):
        with open(pub_path, "rb") as f:
            public_key = f.read()
        try:
            ec2_client.import_key_pair(KeyName=KEY_NAME, PublicKeyMaterial=public_key)
        except ClientError as e:
            if e.response["Error"]["Code"] != "InvalidKeyPair.Duplicate":
                raise
        return KEY_NAME
    response = ec2_client.create_key_pair(KeyName=KEY_NAME)
    with open(PEM_FILE, "w") as f:
        f.write(response["KeyMaterial"])
    os.chmod(PEM_FILE, 0o600)
    return KEY_NAME


def create_security_group(vpc_id):
    """Create security group with SSH (22) and HTTP (80) from 0.0.0.0/0.
    Uses iterated name (devops1-sg-1, devops1-sg-2, ...)."""
    state = _read_state()
    next_index = state.get("next_sg_index", 1)
    name = f"{SECURITY_GROUP_NAME_PREFIX}-{next_index}"
    sg = ec2_client.create_security_group(
        GroupName=name,
        Description="SSH and HTTP for devops1 web server",
        VpcId=vpc_id,
    )
    sg_id = sg["GroupId"]
    for port, desc in [(22, "SSH"), (80, "HTTP")]:
        ec2_client.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[
                {
                    "FromPort": port,
                    "ToPort": port,
                    "IpProtocol": "tcp",
                    "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": desc}],
                }
            ],
        )
    state["next_sg_index"] = next_index + 1
    state.setdefault("security_groups", []).append(sg_id)
    _write_state(state)
    return sg_id


def launch_instance(key_name, security_group_id):
    """Launch EC2 instance with the given key pair and security group."""
    if not os.path.isfile(USERDATA_FILE):
        raise FileNotFoundError(f"UserData script not found: {USERDATA_FILE}")
    with open(USERDATA_FILE, "rb") as f:
        user_data = f.read()
    print(f"Using userdata: {USERDATA_FILE} ({len(user_data)} bytes)")
    try:
        instances = ec2.create_instances(
            ImageId="ami-02dfbd4ff395f2a1b",
            MinCount=1,
            MaxCount=1,
            InstanceType="t2.nano",
            KeyName=key_name,
            SecurityGroupIds=[security_group_id],
            Placement={"AvailabilityZone": "us-east-1b"},
            UserData=user_data,
        )
    except ClientError as e:
        raise RuntimeError(f"Failed to launch instance: {e}") from e
    return instances[0]


def add_instance_to_state(instance_id):
    """Append a created instance ID to the state file for teardown."""
    state = _read_state()
    state.setdefault("instances", [])
    if instance_id not in state["instances"]:
        state["instances"].append(instance_id)
    _write_state(state)


# -----------------------------------------------------------------------------
# S3 website: bucket, logo, index.html, static hosting (part 4)
# -----------------------------------------------------------------------------


def _random_bucket_suffix(length=6):
    """Return a string of random lowercase letters and digits."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def create_s3_bucket_name():
    """Bucket name: kcolville-XXXXXX (globally unique)."""
    return f"{S3_BUCKET_PREFIX}-{_random_bucket_suffix()}"


def create_s3_bucket(bucket_name):
    """Create S3 bucket in REGION. Idempotent if bucket already exists."""
    try:
        s3_client.create_bucket(
            Bucket=bucket_name,
            CreateBucketConfiguration=(
                {"LocationConstraint": REGION} if REGION != "us-east-1" else {}
            ),
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
            raise
    return bucket_name


def download_logo():
    """Download logo from LOGO_URL; handle URL changes generically."""
    req = urllib.request.Request(LOGO_URL, headers={"User-Agent": "devops1.py"})
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def upload_logo_to_bucket(bucket_name, data):
    """Upload logo as logo.jpg with correct Content-Type."""
    s3_client.put_object(
        Bucket=bucket_name,
        Key="logo.jpg",
        Body=data,
        ContentType="image/jpeg",
    )


def create_index_html():
    """Return HTML string that displays the logo (image at logo.jpg)."""
    return """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>S3 Website</title></head>
<body>
<h1>DevOps Assignment 1 - S3 static website</h1>
<p><img src="logo.jpg" alt="Logo" /></p>
</body>
</html>
"""


def upload_index_to_bucket(bucket_name, html_content):
    """Upload index.html for website default document."""
    s3_client.put_object(
        Bucket=bucket_name,
        Key="index.html",
        Body=html_content.encode("utf-8"),
        ContentType="text/html",
    )


def enable_s3_website_hosting(bucket_name):
    """Configure bucket for static website hosting (index document: index.html)."""
    s3_client.put_bucket_website(
        Bucket=bucket_name,
        WebsiteConfiguration={
            "IndexDocument": {"Suffix": "index.html"},
        },
    )


def set_bucket_policy_public_read(bucket_name):
    """Allow public read via bucket policy so website endpoint can serve objects."""
    # New buckets block public access by default; allow policy to grant public read
    s3_client.put_public_access_block(
        Bucket=bucket_name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": False,
            "IgnorePublicAcls": False,
            "BlockPublicPolicy": False,
            "RestrictPublicBuckets": False,
        },
    )
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "PublicReadGetObject",
                "Effect": "Allow",
                "Principal": "*",
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{bucket_name}/*",
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
    website_url = f"http://{bucket_name}.s3-website-{REGION}.amazonaws.com"
    return bucket_name, website_url


# -----------------------------------------------------------------------------
# Output: write URLs to file (part 5)
# -----------------------------------------------------------------------------


def write_websites_file(ec2_url, s3_website_url):
    """Write the two website URLs to kcolville-websites.txt."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), WEBSITES_FILENAME)
    with open(path, "w") as f:
        f.write(f"{ec2_url}\n{s3_website_url}\n")
    print(f"Wrote URLs to {path}")


# -----------------------------------------------------------------------------
# Teardown: terminate instances from state file
# -----------------------------------------------------------------------------


def run_teardown():
    """Terminate each instance in state file and remove from file on success."""
    state = _read_state()
    instances = state.get("instances", [])

    if not instances:
        print("No instances in state file.")
        return

    for instance_id in list(instances):
        try:
            ec2_client.terminate_instances(InstanceIds=[instance_id])
            print(f"Terminated {instance_id}")
            state["instances"].remove(instance_id)
            _write_state(state)
        except ClientError as e:
            print(f"Failed to terminate {instance_id}: {e}")

    print("Teardown complete.")


# -----------------------------------------------------------------------------
# CloudWatch: instance details + metrics from state file
# -----------------------------------------------------------------------------

# Default lookback for metrics (minutes)
CLOUDWATCH_LOOKBACK_MINUTES = 60


def _get_instance_details(instance_id):
    """Return dict with instance state, type, launch time, public ip (or None)."""
    try:
        r = ec2_client.describe_instances(InstanceIds=[instance_id])
        if not r["Reservations"] or not r["Reservations"][0]["Instances"]:
            return None
        inst = r["Reservations"][0]["Instances"][0]
        return {
            "instance_id": instance_id,
            "state": inst["State"]["Name"],
            "instance_type": inst.get("InstanceType", "?"),
            "launch_time": inst.get("LaunchTime"),
            "public_ip": inst.get("PublicIpAddress"),
            "availability_zone": inst.get("Placement", {}).get("AvailabilityZone", "?"),
        }
    except ClientError:
        return None


def _get_cpu_utilization(instance_id, start_time, end_time):
    """Return average and max CPU utilization % for instance in time range, or (None, None)."""
    try:
        r = cloudwatch_client.get_metric_statistics(
            Namespace="AWS/EC2",
            MetricName="CPUUtilization",
            Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
            StartTime=start_time,
            EndTime=end_time,
            Period=300,
            Statistics=["Average", "Maximum"],
        )
        if not r.get("Datapoints"):
            return None, None
        points = r["Datapoints"]
        avg = sum(p["Average"] for p in points) / len(points) if points else None
        mx = max(p["Maximum"] for p in points) if points else None
        return round(avg, 1) if avg is not None else None, (
            round(mx, 1) if mx is not None else None
        )
    except ClientError:
        return None, None


def run_cloudwatch():
    """Print instance details and CloudWatch metrics for each instance in state file."""
    state = _read_state()
    instances = state.get("instances", [])

    if not instances:
        print("No instances in state file.")
        return

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=CLOUDWATCH_LOOKBACK_MINUTES)

    print(
        f"Instance details + CloudWatch metrics (last {CLOUDWATCH_LOOKBACK_MINUTES} min)"
    )
    print("State file:", _state_path())
    print()

    for instance_id in instances:
        details = _get_instance_details(instance_id)
        if not details:
            print(f"  {instance_id}: (could not describe)")
            print()
            continue

        print(f"  Instance ID:   {details['instance_id']}")
        print(f"  State:         {details['state']}")
        print(f"  Type:          {details['instance_type']}")
        print(f"  AZ:            {details['availability_zone']}")
        print(f"  Public IP:     {details['public_ip'] or '-'}")
        if details["launch_time"]:
            print(f"  Launch time:   {details['launch_time']}")
        cpu_avg, cpu_max = _get_cpu_utilization(instance_id, start_time, end_time)
        if cpu_avg is not None and cpu_max is not None:
            print(f"  CPU (avg/max): {cpu_avg}% / {cpu_max}%")
        else:
            print("  CPU (avg/max): (no data yet)")
        print()

    print("Done.")


# -----------------------------------------------------------------------------
# AMI: create image from specified instance (instance ID required)
# -----------------------------------------------------------------------------


def run_ami(instance_id):
    """Create an AMI from the given instance. Name format: XX-YYYY-MM-DD-microseconds."""
    if not instance_id or not instance_id.strip():
        print("Error: instance ID is required.")
        print("Usage: devops1.py ami <instance-id>")
        sys.exit(1)
    instance_id = instance_id.strip()
    now = datetime.now(timezone.utc)
    ami_name = f"{AMI_NAME_PREFIX}-{now.strftime('%Y-%m-%d')}-{now.strftime('%f')[:6]}"
    try:
        r = ec2_client.create_image(
            InstanceId=instance_id,
            Name=ami_name,
            Description=f"AMI from {instance_id} (devops1)",
            NoReboot=True,
        )
        ami_id = r["ImageId"]
        print(f"Creating AMI from instance {instance_id}")
        print(f"AMI ID: {ami_id}")
        print(f"Name: {ami_name}")
        print("AMI is created asynchronously check EC2 console for status.")
    except ClientError as e:
        print(f"Failed to create AMI: {e}")
        sys.exit(1)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def main():
    try:
        # EC2 (parts 1–3)
        print("Getting default VPC...")
        vpc_id = get_default_vpc_id()
        print("Setting up key pair...")
        key_name = create_key_pair()
        print("Creating security group...")
        sg_id = create_security_group(vpc_id)
        print("Launching instance (userdata will run on boot)...")
        instance = launch_instance(key_name, sg_id)
        add_instance_to_state(instance.id)
        print(f"Launched instance: {instance.id}")
        print(f"Key pair saved to: {PEM_FILE}")
        print(f"Security group: {sg_id}")

        # S3 website (part 4)
        print("Setting up S3 website...")
        bucket_name, s3_website_url = setup_s3_website()
        print(f"S3 bucket: {bucket_name}")
        print(f"S3 website URL: {s3_website_url}")

        # Wait for instance to get public IP then write URLs (part 5)
        print("Waiting for instance to be running...")
        instance.wait_until_running()
        instance.reload()
        ec2_url = (
            f"http://{instance.public_ip_address}" if instance.public_ip_address else ""
        )
        write_websites_file(ec2_url, s3_website_url)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        raise
    except ClientError as e:
        err = e.response["Error"]
        print(f'AWS error: {err.get("Code", "Unknown")} - {err.get("Message", str(e))}')
        raise
    except Exception as e:
        print(f"Error: {e}")
        raise


if __name__ == "__main__":
    if len(sys.argv) < 2:
        main()
        sys.exit(0)
    cmd = sys.argv[1].lower()
    if cmd == "teardown":
        run_teardown()
    elif cmd == "cloudwatch":
        run_cloudwatch()
    elif cmd == "ami":
        if len(sys.argv) < 3:
            print("Error: instance ID is required for ami command.")
            print("Usage: devops1.py ami <instance-id>")
            sys.exit(1)
        run_ami(sys.argv[2])
    else:
        print(f"Unknown command: {sys.argv[1]}")
        print("Usage: devops1.py [teardown | cloudwatch | ami <instance-id>]")
        sys.exit(1)
