import os
import logging

if os.path.exists('/tmp/log.txt'):
    with open('/tmp/log.txt', 'r+') as f:
        f.truncate(0)

logging.basicConfig(format='%(asctime)s,%(msecs)03d %(levelname)-8s [%(filename)s:%(lineno)d] %(message)s',
                    datefmt='%Y-%m-%d:%H:%M:%S',
                    handlers=[logging.FileHandler('/tmp/log.txt'), logging.StreamHandler()],
                    level=logging.INFO)

logger = logging.getLogger(__name__)

