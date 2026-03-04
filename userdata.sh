#!/bin/bash
set -e

yum update -y
yum install -y httpd curl

# IMDSv2: get token once (per Assignment 1 Tips Part 1)
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")

# Build index page with metadata using echo/append like the tips example
echo '<!DOCTYPE html><html><head><title>EC2 Instance</title></head><body>' > /var/www/html/index.html
echo '<h1>Instance metadata</h1><ul>' >> /var/www/html/index.html
echo -n '<li>Instance ID: ' >> /var/www/html/index.html
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id >> /var/www/html/index.html
echo '</li>' >> /var/www/html/index.html
echo -n '<li>Private IP: ' >> /var/www/html/index.html
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/local-ipv4 >> /var/www/html/index.html
echo '</li>' >> /var/www/html/index.html
echo -n '<li>Instance type: ' >> /var/www/html/index.html
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-type >> /var/www/html/index.html
echo '</li>' >> /var/www/html/index.html
echo -n '<li>Availability zone: ' >> /var/www/html/index.html
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/placement/availability-zone >> /var/www/html/index.html
echo '</li></ul></body></html>' >> /var/www/html/index.html

systemctl enable httpd
systemctl start httpd
