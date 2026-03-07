#!/bin/bash
set -e

yum update -y
yum install -y httpd

# Get a session token first
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
get_meta() { curl -s -H "X-aws-ec2-metadata-token: $TOKEN" "http://169.254.169.254/latest/meta-data/$1"; }

# Fetch instance metadata
INSTANCE_ID=$(get_meta instance-id)
AMI_ID=$(get_meta ami-id)
INSTANCE_TYPE=$(get_meta instance-type)
AZ=$(get_meta placement/availability-zone)
LOCAL_IP=$(get_meta local-ipv4)
PUBLIC_IP=$(get_meta public-ipv4 || echo "N/A")
HOSTNAME=$(get_meta local-hostname)
REGION=$(get_meta placement/region 2>/dev/null || echo "${AZ%?}")

# Write HTML page with instance info
DOCROOT="/var/www/html"
cat > "$DOCROOT/instance-info.html" << EOF
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>EC2 Instance Metadata</title>
  <style>
    body { font-family: sans-serif; max-width: 600px; margin: 2em auto; padding: 1em; }
    h1 { color: #232f3e; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
    th { background: #232f3e; color: white; }
  </style>
</head>
<body>
  <h1>EC2 Instance Metadata</h1>
  <p>This page is generated from the instance metadata service at boot.</p>
  <img src="https://media.tenor.com/LXfETEPaQ9QAAAAe/whats-up-scary.png" alt="AWS Logo">
  <table>
    <tr><th>Attribute</th><th>Value</th></tr>
    <tr><td>Instance ID</td><td>$INSTANCE_ID</td></tr>
    <tr><td>AMI ID</td><td>$AMI_ID</td></tr>
    <tr><td>Instance Type</td><td>$INSTANCE_TYPE</td></tr>
    <tr><td>Availability Zone</td><td>$AZ</td></tr>
    <tr><td>Region</td><td>$REGION</td></tr>
    <tr><td>Local IPv4</td><td>$LOCAL_IP</td></tr>
    <tr><td>Public IPv4</td><td>$PUBLIC_IP</td></tr>
    <tr><td>Hostname</td><td>$HOSTNAME</td></tr>
  </table>
</body>
</html>
EOF


echo '<meta http-equiv="refresh" content="0;url=instance-info.html">' > "$DOCROOT/index.html"

# Start and enable httpd
systemctl enable httpd
systemctl start httpd
