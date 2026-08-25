import re
import git

name_email_regex = re.compile(r"^(?P<name>[^<]+)?\s*<(?P<email>[^>]+)>$")

def parse_actor(author_str):
    match = name_email_regex.match(author_str)
    if not match:
        raise ValueError("Invalid git author")
    name, email = match.groups()
    if name:
        name = name.strip()
    return git.Actor(name, email)

print(parse_actor("John Doe <john.doe@example.com>").name)
print(parse_actor("John Doe <john.doe@example.com>").email)
