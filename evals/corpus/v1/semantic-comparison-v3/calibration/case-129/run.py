import pickle

with open("downloaded_session.bin", "rb") as f:
    session = pickle.load(f)
