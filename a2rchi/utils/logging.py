import logging


def setup_logging(debug=False):
    if debug:
        level = logging.DEBUG
        format_str = '[%(name)s] %(levelname)s: %(message)s'
    else:
        level = logging.INFO
        format_str = '%(levelname)s: %(message)s'

    logging.basicConfig(level=level, format=format_str)


def get_logger(name):
    return logging.getLogger(name)