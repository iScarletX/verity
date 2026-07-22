import pickle


def load_state(raw_bytes):
    return pickle.loads(raw_bytes)
