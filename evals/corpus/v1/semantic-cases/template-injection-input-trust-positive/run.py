from jinja2 import Template

name = "world"
template = Template(f"Hello {name}!")
output = template.render()
