from os import environ
from logging import getLogger
from os.path import join, dirname
from argparse import ArgumentParser
from time import sleep

from . import jobs
from boto.ec2 import EC2Connection
from requests import get

parser = ArgumentParser(description='Run some source files.')

parser.add_argument('bucketname',
                    help='Required S3 bucket name.')

parser.add_argument('-a', '--access-key', default=environ.get('AWS_ACCESS_KEY_ID', None),
                    help='Optional AWS access key name. Defaults to value of AWS_ACCESS_KEY_ID environment variable.')

parser.add_argument('-s', '--secret-key', default=environ.get('AWS_SECRET_ACCESS_KEY', None),
                    help='Optional AWS secret key name. Defaults to value of AWS_SECRET_ACCESS_KEY environment variable.')

parser.add_argument('--ec2-access-key',
                    help='Optional AWS access key name for setting up EC2; distinct from access key for populating S3 bucket. Defaults to value of S3 access key.')

parser.add_argument('--ec2-secret-key',
                    help='Optional AWS secret key name for setting up EC2; distinct from secret key for populating S3 bucket. Defaults to value of S3 secret key.')

parser.add_argument('--instance-type', default='m3.xlarge',
                    help='EC2 instance type, defaults to m3.xlarge.')

parser.add_argument('--machine-image', default='ami-4ae27e22',
                    help='AMI identifier, defaults to Alestic Ubuntu 14.04 (ami-4ae27e22).')

def main():
    args = parser.parse_args()
    jobs.setup_logger(None)
    
    #
    # Prepare init script for new EC2 instance to run.
    #
    with open(join(dirname(__file__), 'templates', 'user-data.sh')) as file:
        user_data = file.read().format(**args.__dict__)
    
    getLogger('openaddr').info('Prepared {} bytes of instance user data'.format(len(user_data)))

    #
    # Figure out how much we're willing to bid on a spot instance.
    #
    ec2 = EC2Connection(args.ec2_access_key, args.ec2_secret_key)
    history = ec2.get_spot_price_history(instance_type=args.instance_type)
    median = sorted([h.price for h in history])[len(history)/2]
    bid = median + .01

    getLogger('openaddr').info('Bidding ${:.4f}/hour for {} instance'.format(bid, args.instance_type))
    
    #
    # Request a spot instance.
    #
    spot_args = dict(instance_type=args.instance_type, user_data=user_data,
                     key_name='cfa-keypair-2013', security_groups=['default'])

    spot_req = ec2.request_spot_instances(bid, args.machine_image, **spot_args)[0]

    getLogger('openaddr').info('https://console.aws.amazon.com/ec2/v2/home?region=us-east-1#SpotInstances:search={}'.format(spot_req.id))
    
    #
    # Wait while EC2 does its thing, unless the user interrupts.
    #
    try:
        wait_it_out(spot_req)

    except KeyboardInterrupt:
        spot_req = ec2.get_all_spot_instance_requests(spot_req.id)[0]
        
        if spot_req.instance_id:
            print 'Shutting down instance {} early'.format(spot_req.instance_id)
            ec2.terminate_instances(spot_req.instance_id)
        
    finally:
        spot_req.cancel()

def wait_it_out(spot_req):
    ''' Wait for EC2 to finish its work.
    '''
    ec2, logger = spot_req.connection, getLogger('openaddr')
    
    while True:
        sleep(15)
        spot_req = ec2.get_all_spot_instance_requests(spot_req.id)[0]
        if spot_req.state == 'open':
            logger.debug('Spot request {} is open'.format(spot_req.id))
        else:
            break
    
    if spot_req.state != 'active':
        raise Exception('Unexpected spot request state "{}"'.format(spot_req.state))
    
    while True:
        sleep(5)
        spot_req = ec2.get_all_spot_instance_requests(spot_req.id)[0]
        if spot_req.instance_id:
            break
        else:
            logger.debug('Waiting for instance ID')
    
    while True:
        sleep(5)
        instance = ec2.get_only_instances(spot_req.instance_id)[0]
        if instance.public_dns_name:
            break
        else:
            logger.debug('Waiting for instance DNS name')

    logger.info('Found instance {} at {}'.format(instance.id, instance.public_dns_name))

    while True:
        sleep(30)
        instance = ec2.get_only_instances(instance.id)[0]
        if instance.state == 'terminated':
            logger.debug('Instance {} has been terminated'.format(instance.id))
            break
        else:
            logger.debug('Waiting for instance {} to do its work'.format(instance.id))

    logger.info('Job complete')

def kill():
    parser = ArgumentParser(description='Kill EC2 instance from within.')

    parser.add_argument('access_key',
                        help='Required AWS access key.')

    parser.add_argument('secret_key',
                        help='Optional AWS secret key.')

    args = parser.parse_args()

    instance_id = get('http://169.254.169.254/latest/meta-data/instance-id').text.strip()
    EC2Connection(args.access_key, args.secret_key).terminate_instances(instance_id)

if __name__ == '__main__':
    exit(main())
