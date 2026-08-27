import pickle

with open("own_session.bin", "rb") as f:
    session = pickle.load(f)
