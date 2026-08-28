import timeit

setup = """
class Version:
    def __init__(self, is_prerelease, prerelease_token, val):
        self.is_prerelease = is_prerelease
        self.prerelease_token = prerelease_token
        self.val = val
    def __ge__(self, other):
        return self.val >= other.val

version = Version(False, "alpha", 1)
latest_full_release_version = Version(False, "alpha", 0)

class Translator:
    prerelease_token = "beta"

translator = Translator()

historic_versions = [Version(False, "alpha", i) for i in range(1000)]
"""

test_all = """
next(
    filter(
        lambda version: all(
            [
                version.is_prerelease,
                version.prerelease_token == translator.prerelease_token,
                version >= latest_full_release_version,
            ]
        ),
        historic_versions,
    ),
    latest_full_release_version,
)
"""

test_short = """
next(
    filter(
        lambda version: (
            version.is_prerelease
            and version.prerelease_token == translator.prerelease_token
            and version >= latest_full_release_version
        ),
        historic_versions,
    ),
    latest_full_release_version,
)
"""

print("all([...]):", timeit.timeit(test_all, setup=setup, number=10000))
print("short-circuit:", timeit.timeit(test_short, setup=setup, number=10000))
