#!/usr/bin/python3

import boto3
import paramiko
import socket
import time
from botocore.exceptions import ClientError
from contextlib import closing
import func

# Create EC2 Instance in the default VPC
print('Start script')

print('Get list VPCs')
ec2 = boto3.client('ec2')
response = ec2.describe_vpcs(
    Filters=[
        {
            'Name': 'tag:Name',
            'Values': [
                'GrafanaVPC',
            ]
        },
    ]
)

resp = response['Vpcs']
if resp:
    print('VPC exist')
    ec2 = boto3.client('ec2')
    vpc = ec2.describe_vpcs(
        Filters=[
            {
                'Name': 'tag:Name',
                'Values': [
                    'GrafanaVPC',
                ]
            },
        ]
    )
    vpc_id = vpc.get('Vpcs', [{}])[0].get('VpcId', '')
else:
    print('No VPC, creating...')
    ec2 = boto3.client('ec2')
    vpc = ec2.create_vpc(
        CidrBlock='172.16.0.0/16',
        TagSpecifications=[
            {
                'ResourceType': 'vpc',
                'Tags': [
                    {
                        'Key': 'Name',
                        'Value': 'GrafanaVPC'
                    },
                ]
            },
        ],
    )
    vpc = ec2.describe_vpcs(
        Filters=[
            {
                'Name': 'tag:Name',
                'Values': [
                    'GrafanaVPC',
                ]
            },
        ]
    )
    vpc_id = vpc.get('Vpcs', [{}])[0].get('VpcId', '')
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={'Value': True})
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={'Value': True})
    print('Enabled public dns hostname so that we can SSH into it later')
    internetgateway = ec2.create_internet_gateway()
    vpc.attach_internet_gateway(
        InternetGatewayId=internetgateway.get('InternetGateway').get('InternetGatewayId', ''),
        VpcId=vpc_id
    )
    print('Created an internet gateway and attach it to VPC')
    routetable = vpc.create_route_table()
    route = routetable.create_route(
        DestinationCidrBlock='0.0.0.0/0',
        GatewayId=internetgateway.get('InternetGateway').get('InternetGatewayId', ''),
    )
    subnet = ec2.create_subnet(
        CidrBlock='172.16.1.0/24',
        VpcIds=vpc_id,
    )
    print(subnet)
    routetable.associate_with_subnet(SubnetId=subnet.id)
    print('VPC created')

vpc_id = vpc.get('Vpcs', [{}])[0].get('VpcId', '')
print('VpcId = %s.' % vpc_id)
exit()

ec2 = boto3.client('ec2')
instance = ''
try:
    instance = ec2.describe_instances(
        Filters=[
            {
                'Name': 'tag:Name',
                'Values': [
                    'Grafana Dev',
                ]
            },
        ]
    )
except ClientError as e:
    print(e)

ec2 = boto3.resource('ec2')
if instance != '':
    print('Instance already created.')
    instance_id = instance['InstanceId']
else:
    print('Instance does not exist. Creating...')
    instance = ec2.create_instances(
        ImageId='ami-0a5e707736615003c',
        MinCount=1,
        MaxCount=1,
        InstanceType='t2.micro',
        KeyName='ec2-keypair',
        TagSpecifications=[
            {
                'ResourceType': 'instance',
                'Tags': [
                    {
                        'Key': 'Name',
                        'Value': 'Grafana Dev'
                    },
                ]
            },
        ],
    )
    instance_id = instance[0].id
print('EC2 Instance Created with ID %s in vpc %s.' % (instance_id, vpc_id))

# Create Security Group in the existing VPC
ec2 = boto3.client('ec2')

grafana_sg = ''
try:
    grafana_sg = ec2.describe_security_groups(GroupNames=['GrafanaSG'])
except ClientError as e:
    print(e)

if grafana_sg != '':
    print('Security Group GrafanaSG already exist.')
    security_group_id = grafana_sg.get('SecurityGroups', [{}])[0].get('GroupId', '')
else:
    print('SG GrafanaSG does not exist. Creating...')
    try:
        grafana_sg = ec2.create_security_group(GroupName='GrafanaSG',
                                               Description='Grafana Monitoring SG',
                                               VpcId=vpc_id)
        security_group_id = grafana_sg['GroupId']
        print('Security Group Created %s in vpc %s.' % (security_group_id, vpc_id))

        data = ec2.authorize_security_group_ingress(
            GroupId=security_group_id,
            IpPermissions=[
                {'IpProtocol': 'tcp',
                 'FromPort': 3000,
                 'ToPort': 3000,
                 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]},
                {'IpProtocol': 'tcp',
                 'FromPort': 22,
                 'ToPort': 22,
                 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]},
            ]
        )
        print('Ingress Successfully Set %s' % data)
    except ClientError as e:
        print(e)

ec2 = boto3.resource('ec2')
security_group = ec2.SecurityGroup(security_group_id)

tag = security_group.create_tags(
    Tags=[
        {
            'Key': 'Name',
            'Value': 'GrafanaSG'
        },
    ]
)

# Attaching Security Group to the EC2 Instance
instance = ec2.Instance(instance_id)
instance.modify_attribute(
    Groups=[
        security_group_id
    ]
)
print('EC2 Instance ID %s attached to Security Group GrafanaSG.' % instance_id)

volume = ec2.create_volume(
    AvailabilityZone='us-east-1a',
    Size=1,
    VolumeType='standard',
    TagSpecifications=[
        {
            'ResourceType': 'volume',
            'Tags': [
                {
                    'Key': 'Name',
                    'Value': 'GrafanaVolume'
                },
            ]
        },
    ]
)

# Reload the instance attributes
instance.load()

instance_hostname = instance.public_dns_name
instance_ip = instance.public_ip_address

print('Public DNS Name %s \nWait until the instance is ready...' % instance_hostname)
instance.wait_until_running(
    Filters=[
        {
            'Name': 'instance-id',
            'Values': [
                instance_id
            ],
        },
    ]
)

# Attach volume to the instance
volume.attach_to_instance(
    Device='/dev/sdf',
    InstanceId=instance_id
)
print('Volume %s is attached to the instance %s' % (volume, instance_id))

# Check Network Connectivity
is_port_open = False
while not is_port_open:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        if sock.connect_ex((instance_ip, 22)) == 0:
            print('Port %s on %s is open' % ('22', instance_ip))
            is_port_open = True
        else:
            print('Port %s on %s is not open \n Wait until port 22 is open...' % ('22', instance_ip))
            time.sleep(3)

# connect by SSH to the instance
sshclient = paramiko.SSHClient()
sshclient.set_missing_host_key_policy(paramiko.AutoAddPolicy())

try:
    # using username, public dns name and the pem file create connection to the instance
    sshclient.connect(hostname=instance_hostname, username='centos', pkey=None, key_filename='centos.pem')

    # Execute command remotely
    stdin, stdout, stderr = sshclient.exec_command('mkdir -p /home/centos/grafana')
    stdin, stdout, stderr = sshclient.exec_command('sudo parted -s /dev/sdf mklabel gpt -- mkpart primary ext4 1 -1')
    stdin, stdout, stderr = sshclient.exec_command('sudo mkfs -t ext4 /dev/sdf')
    stdin, stdout, stderr = sshclient.exec_command('sudo mount /dev/sdf /home/centos/grafana')
    stdin, stdout, stderr = sshclient.exec_command('sudo yum -y install git nano python3 python3-pip')
    stdin, stdout, stderr = sshclient.exec_command('git --version; nano --version; python3 --version; pip3 --version')
    print('Git installed, %s' % stdout.read())
    stdin, stdout, stderr = sshclient.exec_command('sudo chown centos:centos -R /home/ec2-user/grafana')
    stdin, stdout, stderr = sshclient.exec_command(
        'git config --global user.name "Daniil Mironuyk" && git config --global user.email "daniilmironuyk@gmail.com"')
    print('Changed --global user.name and user.email')
    print('Grafana started on %s' % instance_hostname)
    sshclient.close()
except Exception as e:
    print(e)
