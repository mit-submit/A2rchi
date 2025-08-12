from a2rchi.utils.config_loader import load_config
import logging
import yaml

# ignore debug logs from these modules, too verbose :)
ignore_debug_modules = ["urllib3.connectionpool", "filelock"]

config = load_config(map=False)
verbosity = config["verbosity"]

logging_verboseLevel = [
    logging.CRITICAL,
    logging.ERROR,
    logging.WARNING,
    logging.INFO,
    logging.DEBUG,
]

def setup_logging():

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


def get_logger(name):
    return logging.getLogger(name)