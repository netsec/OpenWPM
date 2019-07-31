from __future__ import absolute_import

import os
import time

import boto3
import sentry_sdk
from six.moves import range

from automation import CommandSequence, TaskManager
from automation.utilities import rediswq
from automation.utilities.sentry import activate_sentry
from test.utilities import LocalS3Session, local_s3_bucket

# Configuration via environment variables
NUM_BROWSERS = int(os.getenv('NUM_BROWSERS', '1'))
REDIS_QUEUE_NAME = os.getenv('REDIS_QUEUE_NAME', 'crawl-queue')
CRAWL_DIRECTORY = os.getenv('CRAWL_DIRECTORY', 'crawl-data')
S3_BUCKET = os.getenv('S3_BUCKET', 'openwpm-crawls')
HTTP_INSTRUMENT = os.getenv('HTTP_INSTRUMENT', '1') == '1'
COOKIE_INSTRUMENT = os.getenv('COOKIE_INSTRUMENT', '1') == '1'
NAVIGATION_INSTRUMENT = os.getenv('NAVIGATION_INSTRUMENT', '1') == '1'
JS_INSTRUMENT = os.getenv('JS_INSTRUMENT', '1') == '1'
SAVE_JAVASCRIPT = os.getenv('SAVE_JAVASCRIPT', '0') == '1'
DWELL_TIME = int(os.getenv('DWELL_TIME', '10'))
TIMEOUT = int(os.getenv('TIMEOUT', '60'))
SENTRY_DSN = os.getenv('SENTRY_DSN', None)

# Activate Sentry if configured
if SENTRY_DSN:
    activate_sentry(dsn=SENTRY_DSN)
    sentry_sdk.capture_message("Crawl worker started")

# Loads the default manager params
# and NUM_BROWSERS copies of the default browser params
manager_params, browser_params = TaskManager.load_default_params(NUM_BROWSERS)

# Browser configuration
for i in range(NUM_BROWSERS):
    browser_params[i]['http_instrument'] = HTTP_INSTRUMENT
    browser_params[i]['cookie_instrument'] = COOKIE_INSTRUMENT
    browser_params[i]['navigation_instrument'] = NAVIGATION_INSTRUMENT
    browser_params[i]['js_instrument'] = JS_INSTRUMENT
    browser_params[i]['save_javascript'] = SAVE_JAVASCRIPT
    browser_params[i]['headless'] = True

# Manager configuration
manager_params['data_directory'] = '~/Desktop/%s/' % CRAWL_DIRECTORY
manager_params['log_directory'] = '~/Desktop/%s/' % CRAWL_DIRECTORY
manager_params['output_format'] = 's3'
manager_params['s3_bucket'] = S3_BUCKET
manager_params['s3_directory'] = CRAWL_DIRECTORY

# Allow the use of localstack's mock s3 service
S3_ENDPOINT = os.getenv('S3_ENDPOINT')
if S3_ENDPOINT:
    boto3.DEFAULT_SESSION = LocalS3Session(endpoint_url=S3_ENDPOINT)
    manager_params['s3_bucket'] = local_s3_bucket(
        boto3.resource('s3'), name=S3_BUCKET)

# Instantiates the measurement platform
# Commands time out by default after 60 seconds
manager = TaskManager.TaskManager(manager_params, browser_params)

# Connect to job queue
job_queue = rediswq.RedisWQ(name=REDIS_QUEUE_NAME, host="redis")
manager.logger.info("Worker with sessionID: %s" % job_queue.sessionID())
manager.logger.info("Initial queue state: empty=%s" % job_queue.empty())

# Crawl sites specified in job queue until empty
while not job_queue.empty():
    job = job_queue.lease(lease_secs=120, block=True, timeout=5)
    if job is None:
        manager.logger.info("Waiting for work")
        time.sleep(5)
    else:
        site_rank, site = job.decode("utf-8").split(',')
        if "://" not in site:
            site = "http://" + site
        manager.logger.info("Visiting %s..." % site)
        command_sequence = CommandSequence.CommandSequence(
            site, reset=True
        )
        command_sequence.get(sleep=DWELL_TIME, timeout=TIMEOUT)
        manager.execute_command_sequence(command_sequence)
        job_queue.complete(job)

manager.logger.info("Job queue finished, exiting.")
manager.close()

if SENTRY_DSN:
    sentry_sdk.capture_message("Crawl worker finished")
