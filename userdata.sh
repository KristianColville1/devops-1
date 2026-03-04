#!/bin/bash
set -e

yum update -y
yum install -y httpd

# Fetch instance metadata (AWS IMDS - link-local, instance-only)
METADATA_BASE='http://169.254.169.254/latest/meta-data'
INSTANCE_ID=$(curl -s "$METADATA_BASE/instance-id")
LOCAL_IP=$(curl -s "$METADATA_BASE/local-ipv4")
INSTANCE_TYPE=$(curl -s "$METADATA_BASE/instance-type")
AZ=$(curl -s "$METADATA_BASE/placement/availability-zone")

# Write index page with metadata
cat > /var/www/html/index.html << EOF
<!DOCTYPE html>
<html>
<head><title>EC2 Instance</title></head>
<body>
<h1>Instance metadata</h1>
<ul>
<li>Instance ID: $INSTANCE_ID</li>
<li>Private IP: $LOCAL_IP</li>
<li>Instance type: $INSTANCE_TYPE</li>
<li>Availability zone: $AZ</li>
</ul>
</body>
</html>
EOF

systemctl enable httpd
systemctl start httpd
