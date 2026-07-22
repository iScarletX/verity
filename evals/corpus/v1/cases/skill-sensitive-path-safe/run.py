import os


def load_config():
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "config.json")) as f:
        return f.read()
