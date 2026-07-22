import os

API_KEY = os.environ.get("API_KEY")


def call_api():
    return API_KEY
