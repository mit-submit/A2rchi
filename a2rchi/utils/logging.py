from a2rchi.utils.config_loader import load_global_config
import logging
import yaml

# ignore debug logs from these modules, too verbose :)
ignore_debug_modules = ["urllib3.connectionpool", "filelock"]

logging_verboseLevel = [
    logging.CRITICAL,
    logging.ERROR,
    logging.WARNING,
    logging.INFO,
    logging.DEBUG,
]

def setup_logging():

    config = load_global_config()
    verbosity = config["verbosity"]

    format_str = '(%(asctime)s) [%(name)s] %(levelname)s: %(message)s'

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

def setup_cli_logging(verbosity):
    
    if verbosity > 3: # high verbose mode
        format_str = '[%(name)s] %(levelname)s: %(message)s'
    else: # low verbose mode
        format_str = '[a2rchi] %(message)s'
    level = logging_verboseLevel[max(0, min(4, verbosity))]
    logging.basicConfig(
        level=level,
        format=format_str,
        force=True
    )

def get_logger(name):
    return logging.getLogger(name)