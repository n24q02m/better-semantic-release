import re

ACTOR_REGEX = re.compile(r"^(?P<name>[^<]+)?\s*<(?P<email>[^>]+)>$")

def validate_actor(author_str):
    match = ACTOR_REGEX.match(author_str)
    if not match:
        raise ValueError(
            f"Invalid git author: {author_str} "
            f"should match {ACTOR_REGEX}"
        )
    return match.groups()

print(validate_actor("John Doe <john.doe@example.com>"))
