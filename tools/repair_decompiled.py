# -*- coding: utf-8 -*-
"""Repair uncompyle6 damage in decompiled Manager.py:
1) splice recovered literal lists (LOAD_CONST indices -> real strings)
2) replace module-level bare `return` (outside any def) with `pass`
"""
import json, re, sys

path = 'src/Offline/Manager.py'
lines = open(path).read().split('\n')
rec = json.load(open('src/decompiled_0.9.22/recovered.json'))

def find_block(start_pred, open_ch='[', close_ch=']'):
    for i, ln in enumerate(lines):
        if start_pred(ln):
            depth = 0
            for j in range(i, len(lines)):
                depth += lines[j].count(open_ch) - lines[j].count(close_ch)
                if j > i and depth <= 0:
                    return i, j
                if depth <= 0 and open_ch in lines[j] and close_ch in lines[j]:
                    return i, j
    raise SystemExit('block not found')

def fmt_list(items, indent, per_line=4):
    out = []
    for k in range(0, len(items), per_line):
        out.append(indent + ' '.join(repr(x) + ',' for x in items[k:k+per_line]))
    return out

# --- tank_list ---
i, j = find_block(lambda ln: re.match(r'\s*tank_list = \[', ln))
indent = re.match(r'\s*', lines[i]).group(0)
new = [indent + 'tank_list = ['] + fmt_list(rec['tank'], indent + ' ', 3) + [indent + ']']
lines[i:j+1] = new

# --- _int_keys ---
i, j = find_block(lambda ln: '_int_keys = set([' in ln)
indent = re.match(r'\s*', lines[i]).group(0)
new = [indent + '_int_keys = set(['] + fmt_list(rec['intkeys'], indent + ' ', 4) + [indent + '])']
lines[i:j+1] = new

# --- mocked_methods ---
i, j = find_block(lambda ln: re.match(r'\s*mocked_methods = \[', ln))
indent = re.match(r'\s*', lines[i]).group(0)
new = [indent + 'mocked_methods = ['] + fmt_list(rec['mocked'], indent + ' ', 4) + [indent + ']']
lines[i:j+1] = new

# --- module-level bare returns -> pass ---
def fix_returns(lines):
    out = []
    stack = []  # (indent, kind) kind in {'def','other'}
    for ln in lines:
        stripped = ln.strip()
        if stripped == '' or stripped.startswith('#'):
            out.append(ln)
            continue
        indent = len(ln) - len(ln.lstrip(' '))
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if stripped == 'return' or stripped.startswith('return '):
            in_def = any(kind == 'def' for _, kind in stack)
            if not in_def:
                ln = ln[:indent] + 'pass'
        if re.match(r'(def |class )', stripped) and stripped.endswith(':') or re.match(r'def \w+\(.*\):', stripped):
            stack.append((indent, 'def' if stripped.startswith('def ') else 'other'))
        elif stripped.endswith(':') and re.match(r'(if |elif |else|try|except|finally|for |while |with )', stripped):
            stack.append((indent, 'other'))
        out.append(ln)
    return out

lines = fix_returns(lines)
open(path, 'w').write('\n'.join(lines))
print('repaired; lines:', len(lines))
