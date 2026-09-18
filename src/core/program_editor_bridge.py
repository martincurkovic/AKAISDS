from core import app_config
from s3k.bridge import S3kBridge


def connect():
    _input_name, output_name = app_config.get_saved_ports()
    return S3kBridge.standard(output_name)
