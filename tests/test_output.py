import subprocess
import sys

from ddnetmirror.output import shown


def test_offline_modules_do_not_load_collection_modules():
    # a fresh interpreter, so modules loaded by other tests cannot hide an import
    code = (
        "import sys, ddnetmirror.fixup, ddnetmirror.topology; "
        "print(sorted(m for m in ('ddnetmirror.fetch_config', 'ddnetmirror.lldp') if m in sys.modules))"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "[]"


def test_shown_escapes_control_bidi_and_zero_width_characters():
    assert shown("a\x1bb\x85c") == "a\\x1bb\\x85c"
    assert shown("leaf\u202e1\u200b\ufeff") == "leaf\\u202e1\\u200b\\ufeff"
    assert shown("caf\u00e9 \U000e0001") == "caf\u00e9 \\U000e0001"  # letters stay
