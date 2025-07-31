from a2rchi.utils.config_loader import load_config_file
import logging
import yaml

# ignore debug logs from these modules, too verbose :)
ignore_debug_modules = ["urllib3.connectionpool", "filelock"]

config = load_config_file()
verbosity = config["verbosity"]

logging_verboseLevel = [
    logging.CRITICAL,
    logging.ERROR,
    logging.WARNING,
    logging.INFO,
    logging.DEBUG,
]

def setup_logging():

    debug = False # this is for the flask app
    if verbosity == 4:
        debug = True
        format_str = '[%(name)s] %(levelname)s: %(message)s'
    else:
        format_str = '%(levelname)s: %(message)s'

    level = logging_verboseLevel[max(0, min(4, verbosity))]
    logging.basicConfig(
        level=level,
        format=format_str,
        force=True
    )

    # need to override werkzeug which Flask uses
    logging.getLogger('werkzeug').setLevel(level)

    if verbosity == 4:
        for module in ignore_debug_modules:
            logging.getLogger(module).setLevel(logging_verboseLevel[3])

    return debug

def get_logger(name):
    return logging.getLogger(name)