#!/bin/bash
set -e

yum update -y
yum install -y httpd curl

# Wait for instance metadata service then fetch (IMDSv2 on AL2023)
METADATA_BASE='http://169.254.169.254/latest/meta-data'
TOKEN=''
for i in 1 2 3 4 5 6 7 8 9 10; do
  TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" --connect-timeout 2 --max-time 3 2>/dev/null) && break
  sleep 2
done

if [ -n "$TOKEN" ]; then
  INSTANCE_ID=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" "$METADATA_BASE/instance-id" 2>/dev/null || echo 'n/a')
  LOCAL_IP=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" "$METADATA_BASE/local-ipv4" 2>/dev/null || echo 'n/a')
  INSTANCE_TYPE=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" "$METADATA_BASE/instance-type" 2>/dev/null || echo 'n/a')
  AZ=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" "$METADATA_BASE/placement/availability-zone" 2>/dev/null || echo 'n/a')
else
  INSTANCE_ID=$(curl -s "$METADATA_BASE/instance-id" 2>/dev/null || echo 'n/a')
  LOCAL_IP=$(curl -s "$METADATA_BASE/local-ipv4" 2>/dev/null || echo 'n/a')
  INSTANCE_TYPE=$(curl -s "$METADATA_BASE/instance-type" 2>/dev/null || echo 'n/a')
  AZ=$(curl -s "$METADATA_BASE/placement/availability-zone" 2>/dev/null || echo 'n/a')
fi

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
<img src="https://media.tenor.com/LXfETEPaQ9QAAAAe/whats-up-scary.png" alt="AWS Logo">
</body>
</html>
EOF

systemctl enable httpd
systemctl start httpd
