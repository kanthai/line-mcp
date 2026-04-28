#!/usr/bin/env python3
"""
Patch /usr/lib/waydroid/tools/helpers/arch.py to return 'arm64' instead of
'arm64_only' on pure AArch64 CPUs.

This allows waydroid init to fetch waydroid_arm64 (Android 11) images which
don't have the ExternalStorageServiceImpl/mediaserver deadlock issue.

Must be run as root (sudo).
"""
import sys, shutil, pathlib

TARGET = pathlib.Path("/usr/lib/waydroid/tools/helpers/arch.py")

OLD = """    elif target == "arm64" and not is_32bit_capable():
        logging.info("AArch64 CPU does not appear to support AArch32, assuming arm64_only...")
        return "arm64_only\""""

NEW = """    elif target == "arm64" and not is_32bit_capable():
        logging.info("AArch64 CPU: is_32bit_capable=False, but forcing arm64 for waydroid_arm64 images")
        # arm64_only skipped — waydroid_arm64 images have 32-bit libs that just never execute
        pass  # return "arm64_only\""""

src = TARGET.read_text()
if "forcing arm64 for waydroid_arm64 images" in src:
    print("Already patched.")
    sys.exit(0)

if OLD not in src:
    print("ERROR: expected text not found — arch.py may have changed.")
    print("Looking for:")
    print(repr(OLD))
    sys.exit(1)

backup = TARGET.with_suffix(".py.bak")
shutil.copy2(TARGET, backup)
print(f"Backup: {backup}")

TARGET.write_text(src.replace(OLD, NEW))
print("Patched successfully.")
print("Now run: sudo waydroid init -f -c https://ota.waydro.id/system -v https://ota.waydro.id/vendor")
