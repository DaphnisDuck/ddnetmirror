import subprocess
import sys


def test_offline_modules_do_not_load_collection_modules():
    # a fresh interpreter, so modules loaded by other tests cannot hide an import
    code = (
        "import sys, ddnetmirror.fixup, ddnetmirror.topology; "
        "print(sorted(m for m in ('ddnetmirror.fetch_config', 'ddnetmirror.lldp') if m in sys.modules))"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "[]"
