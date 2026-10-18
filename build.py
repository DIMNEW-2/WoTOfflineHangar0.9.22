#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Builds the WoT 0.9.21.0.1 Offline Hangar mod: compiles the Python 2.7
sources in src/ to .pyc files in res_mods/0.9.21.0.1/ (same layout as the
original 0.9.22 mod shipped).

Requires a Python 2.7 interpreter. Point WOT_PY27 at it if it is not found
automatically:
    WOT_PY27=/path/to/python2.7 python3 build.py
"""
import os
import shutil
import subprocess
import sys

# The 0.9.21 micropatches share the same scripts; only the res_mods
# folder name differs (.0.1 vs .0.2 clients), so build both.
TARGET_VERSIONS = ['0.9.21.0.1', '0.9.21.0.2']
ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, 'src')

FILES = [
    'Offline/__init__.py',
    'Offline/Manager.py',
    'Offline/NewYearServer.py',
    'Offline/MPChat.py',
    'gui/Scaleform/daapi/view/login/LoginView.py',
]

_COMPILE_SNIPPET = (
    "import py_compile, sys\n"
    "py_compile.compile(sys.argv[1], cfile=sys.argv[2], doraise=True)\n"
    "print('compiled: ' + sys.argv[2])\n"
)


def find_python27():
    candidates = [os.environ.get('WOT_PY27'), '/tmp/py27/bin/python',
                  shutil.which('python2.7'), shutil.which('python2')]
    for cand in candidates:
        if cand and os.path.isfile(cand):
            out = subprocess.run([cand, '-c', 'import sys; print(sys.version_info[:2])'],
                                 capture_output=True, text=True)
            if '(2, 7)' in out.stdout:
                return cand
    return None


def main():
    py27 = find_python27()
    if not py27:
        sys.exit('ERROR: Python 2.7 interpreter not found. '
                 'Set WOT_PY27=/path/to/python2.7')
    print('Using Python 2.7: %s' % py27)
    for version in TARGET_VERSIONS:
        dst_root = os.path.join(ROOT, 'res_mods', version, 'scripts', 'client')
        for rel in FILES:
            src = os.path.join(SRC, rel)
            dst = os.path.join(dst_root, rel[:-3] + '.pyc')  # .py -> .pyc
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            subprocess.run([py27, '-c', _COMPILE_SNIPPET, src, dst], check=True)
            with open(dst, 'rb') as f:
                magic = f.read(4)
            if magic != b'\x03\xf3\r\n':
                sys.exit('ERROR: unexpected .pyc magic in %s: %r' % (dst, magic))
        # Ship the multiplayer config template next to the mod scripts
        # (res_mods/<version>/mp_config.txt is one of the checked paths).
        cfg_src = os.path.join(ROOT, 'mp_config.txt')
        if os.path.isfile(cfg_src):
            cfg_dst = os.path.join(ROOT, 'res_mods', version, 'mp_config.txt')
            shutil.copyfile(cfg_src, cfg_dst)
            print('copied: ' + cfg_dst)
    print('Done. Install: copy the res_mods/<your client micropatch>/ '
          'folder (%s) into your World of Tanks folder.' % ' or '.join(TARGET_VERSIONS))


if __name__ == '__main__':
    main()
