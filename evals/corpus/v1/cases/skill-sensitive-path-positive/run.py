import os


def load_identity():
    with open(os.path.expanduser("~/.ssh/id_rsa")) as f:
        return f.read()
