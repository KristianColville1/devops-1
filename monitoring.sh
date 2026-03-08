#!/usr/bin/bash
#
# Monitoring for EC2 instance. Tested on Amazon Linux 2023.
#
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
INSTANCE_ID=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id)
MEMORYUSAGE=$(free -m | awk 'NR==2{printf "%.2f%%", $3*100/$2 }')
PROCESSES=$(expr $(ps -A | grep -c .) - 1)
HTTPD_PROCESSES=$(ps -A | grep -c httpd || true)

echo "--- Instance ---"
echo "Instance ID: $INSTANCE_ID"
echo "Uptime: $(uptime -p 2>/dev/null || uptime)"
echo ""
echo "--- Resources ---"
echo "Memory utilisation: $MEMORYUSAGE"
echo "No of processes: $PROCESSES"
echo ""
echo "--- Web server (Apache/httpd) ---"
if [ "$HTTPD_PROCESSES" -ge 1 ] 2>/dev/null; then
    echo "Status: running ($HTTPD_PROCESSES httpd process(es))"
    echo "Version: $(httpd -v 2>/dev/null | head -1 || apachectl -v 2>/dev/null | head -1 || echo 'unknown')"
else
    echo "Status: NOT running"
fi