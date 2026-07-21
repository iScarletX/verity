from urllib.request import urlopen
print(urlopen("https://example.invalid/health").status)
