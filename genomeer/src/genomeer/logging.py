import logging, sys
def setup_logger(name="genomeer", level=logging.INFO):
    logger = logging.getLogger(name)
    if not logger.handlers:
        h = logging.StreamHandler(sys.stdout)
        fmt = logging.Formatter("[%(asctime)s][%(levelname)s] %(message)s")
        h.setFormatter(fmt)
        logger.addHandler(h)
        logger.setLevel(level)
    return logger
