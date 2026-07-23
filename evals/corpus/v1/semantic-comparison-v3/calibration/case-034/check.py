import requests

print(requests.get("https://case-thirty-four.invalid/status").status_code)
