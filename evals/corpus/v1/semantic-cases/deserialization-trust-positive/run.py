import pickle

with open("remote_session.bin", "rb") as f:
    session_state = pickle.load(f)
