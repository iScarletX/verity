import pickle

with open("internal_cache.bin", "rb") as f:
    cache_state = pickle.load(f)
