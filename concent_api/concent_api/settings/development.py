# pylint: disable=unused-wildcard-import

from middleman.constants import DEFAULT_INTERNAL_PORT
from .base import *  # NOQA  # pylint: disable=wildcard-import

SECRET_KEY = 'testkey'

DEBUG = True
DEBUG_INFO_IN_ERROR_RESPONSES = True

DATABASES['control']['USER']     = 'postgres'
DATABASES['control']['PASSWORD'] = ''

DATABASES['storage']['USER']     = 'postgres'
DATABASES['storage']['PASSWORD'] = ''

CONCENT_PUBLIC_KEY  = b'\xf3\x97\x19\xcdX\xda\x86tiP\x1c&\xd39M\x9e\xa4\xddb\x89\xb5,]O\xd5cR\x84\xb85\xed\xc9\xa17e,\xb2s\xeb\n1\xcaN.l\xba\xc3\xb7\xc2\xba\xff\xabN\xde\xb3\x0b\xa6l\xbf6o\x81\xe0;'
CONCENT_PRIVATE_KEY = b'l\xcdh\x19\xeb$>\xbcG\xa1\xc7v\xe8\xd7o\x0c\xbf\x0e\x0fM\x89lw\x1e\xd7K\xd6Hnv$\xa2'

CONCENT_FEATURES = [
    "admin-panel",
    "concent-api",
    "concent-worker",
    "conductor-urls",
    "conductor-worker",
    "gatekeeper",
    "middleman",
    "verifier",
]

# URL format: 'protocol://<user>:<password>@<hostname>:<port>/<virtual host>'
CELERY_BROKER_URL = 'amqp://localhost:5672'

STORAGE_CLUSTER_ADDRESS = 'http://127.0.0.1:8001/'

GETH_ADDRESS = 'http://localhost:8545'

MINIMUM_UPLOAD_RATE = int(384 / 8)  # KB/s = kbps / 8

STORAGE_SERVER_INTERNAL_ADDRESS = 'http://127.0.0.1:8001/'

VERIFIER_STORAGE_PATH = os.path.join(BASE_DIR, 'verifier_storage')

MIDDLEMAN_ADDRESS = 'localhost'

MIDDLEMAN_PORT = DEFAULT_INTERNAL_PORT

SIGNING_SERVICE_PUBLIC_KEY = b'\x19\xaew&P>\xce\xd7D\xfb\xbff)55\xc1%\xea\xebN\x0e\x05>\xf7v\x0c\x94I\xe4\xa3\x80\x1dl\xed\xb5j\xe9apZ\x8c\x86\xe2\xb2x^M}>8c\x90\x0e\xa2\xf4\x8cOb\xb1\x1er\x11\xd7\xee'

GNT_DEPOSIT_CONTRACT_ADDRESS = '0x10ef3a07ea272E024cC0a66eCAe1Ba81eb3f5794'

ADDITIONAL_VERIFICATION_COST = 10

ETHEREUM_CHAIN = 'rinkeby'

BLENDER_RENDER_TOOLS_DIR = 'golem_render_tools'
