import yaml

with open("bundled_settings.yaml", "r") as f:
    config = yaml.load(f, Loader=yaml.FullLoader)
