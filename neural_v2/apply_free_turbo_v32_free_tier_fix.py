#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path('neural-upstream').resolve()
p = root / 'app/src/main/java/com/pv/androidfacefusion/HfZeroGpuProvisioner.java'
s = p.read_text(encoding='utf-8')
old = '        create.put("private", false);\n'
new = '        create.put("private", false);\n        // Create directly on the free ZeroGPU flavor. Do not create a paid/CPU tier first.\n        create.put("hardware", "zero-a10g");\n'
if old not in s:
    raise SystemExit('Free-tier hardware creation anchor missing')
s = s.replace(old, new, 1)
p.write_text(s, encoding='utf-8')
if 'create.put("hardware", "zero-a10g")' not in s:
    raise SystemExit('ZeroGPU create guard failed')
print('FREE_TURBO_ZERO_GPU_CREATE_OK')
