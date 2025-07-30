import logging

# ignore debug logs from these modules, too verbose :)
ignore_debug_modules = ["urllib3.connectionpool", "filelock"]

def setup_logging(debug=False):

    if debug:
        level = logging.DEBUG
        format_str = '[%(name)s] %(levelname)s: %(message)s'
    else:
        level = logging.INFO
        format_str = '%(levelname)s: %(message)s'

    logging.basicConfig(level=level, format=format_str)

    if debug:
        for module in ignore_debug_modules:
            logging.getLogger(module).setLevel(logging.INFO)

def get_logger(name):
    return logging.getLogger(name)