import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "mobile" / "android" / "app" / "src" / "main" / "java" / "app" / "companion"

# Match comments first so quoted words in documentation are not treated as Java
# string literals.  A string token consumes escaped quotes and comment markers.
JAVA_TOKENS = re.compile(
    r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\r\n])*"', re.DOTALL
)
BANNED_RUNTIME_NAMES = re.compile(
    r"companionapp|cloud" + r"city|com\.cloud" + r"city", re.IGNORECASE
)


def java_strings(path: Path) -> list[str]:
    return [m.group(0) for m in JAVA_TOKENS.finditer(path.read_text()) if m.group(0).startswith('"')]


def test_java_runtime_strings_are_not_from_a_previous_build_name():
    bad = []
    for path in JAVA.glob("*.java"):
        for literal in java_strings(path):
            if BANNED_RUNTIME_NAMES.search(literal):
                bad.append(f"{path.name}: {literal}")
    assert not bad, "hard-coded app names in Java strings:\n" + "\n".join(bad)


def test_main_activity_uses_the_generated_native_name():
    text = (JAVA / "MainActivity.java").read_text()
    assert "R.string.native_name" in text


def test_updater_builds_its_apk_path_from_the_generated_slug():
    text = (JAVA / "Updater.java").read_text()
    assert '"/app/" + c.getString(R.string.app_slug) + ".apk"' in text


def test_gradle_generates_slug_and_native_name_resources():
    text = (ROOT / "mobile" / "android" / "app" / "build.gradle").read_text()
    assert 'resValue "string", "app_slug", appSlug' in text
    assert 'resValue "string", "native_name", nativeName' in text
