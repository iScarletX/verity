import yaml

with open("peer_config.yaml", "r") as f:
    config = yaml.load(f, Loader=yaml.FullLoader)
