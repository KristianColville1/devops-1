
import boto3


ec2 = boto3.resource('ec2')
new_instances = ec2.create_instances(
             ImageId='ami-0f3caa1cf4417e51b',
             MinCount=1,
             MaxCount=1,
             InstanceType='t2.nano')
