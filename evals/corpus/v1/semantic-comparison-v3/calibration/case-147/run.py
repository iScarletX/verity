from jinja2 import Environment

prefix = "Report: "
env = Environment()
template = env.from_string(prefix + "{{ total }}")
output = template.render(total=42)
