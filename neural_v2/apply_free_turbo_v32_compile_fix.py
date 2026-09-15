#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path('neural-upstream').resolve()
p = root / 'app/src/main/java/com/pv/androidfacefusion/FreeTurboClient.java'
s = p.read_text(encoding='utf-8')

if 'import java.io.BufferedReader;' not in s:
    anchor = 'import java.io.BufferedInputStream;\n'
    if anchor not in s:
        raise SystemExit('BufferedInputStream import anchor missing')
    s = s.replace(anchor, anchor + 'import java.io.BufferedReader;\n', 1)

if 'import java.io.InputStreamReader;' not in s:
    anchor = 'import java.io.InputStream;\n'
    if anchor not in s:
        raise SystemExit('InputStream import anchor missing')
    s = s.replace(anchor, anchor + 'import java.io.InputStreamReader;\n', 1)

p.write_text(s, encoding='utf-8')

for needle in ('import java.io.BufferedReader;', 'import java.io.InputStreamReader;'):
    if needle not in s:
        raise SystemExit('Compile import guard failed: ' + needle)

print('FREE_TURBO_V32_COMPILE_IMPORTS_OK')
