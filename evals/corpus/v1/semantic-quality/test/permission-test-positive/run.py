from urllib.request import urlopen
print(urlopen("https://example.invalid/test-data").read())
