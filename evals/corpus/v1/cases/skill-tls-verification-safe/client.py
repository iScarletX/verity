import requests


def fetch_status(url):
    return requests.get(url)
