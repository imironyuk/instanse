### Before running script

1. Configure AWS CLI according to the [official guide](https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html) set region to `us-east-1`
3. Install `boto3` and `botocore` python libraries

### Script execution

1. Get default VPC ID
2. Idempotent Security Group verification (create if not exist or get Security Group ID)
3. Create SSH key Pair in AWS and get Private Key
4. Create Instance `t2.micro` with SG and Keypair that we created earlier
5. We expect the availability of Instance via SSH protocol which works on port 22
6. Copy the `script.sh` to the instance and run it on it
