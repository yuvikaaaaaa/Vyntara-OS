import ast, os, sys

d = 'backend/app/planner'
errors = []
files = []

for f in sorted(os.listdir(d)):
    if not f.endswith('.py'):
        continue
    path = os.path.join(d, f)
    src = open(path).read()
    lines = len(src.splitlines())
    files.append((f, lines))
    try:
        tree = ast.parse(src)
        dup_found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                names = [n.name for n in node.body if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))]
                dupes = {n for n in names if names.count(n) > 1}
                if dupes:
                    print(f'  ⚠️  {f}: duplicate methods in {node.name}: {dupes}')
                    dup_found = True
        status = '✅' if not dup_found else '⚠️ '
        print(f'  {status}  {f:<26} ({lines:>4} lines)')
    except SyntaxError as e:
        errors.append((f, e))
        print(f'  ❌  {f}: {e}')

total_lines = sum(l for _, l in files)
print()
print(f'Files : {len(files)}/14')
print(f'Lines : {total_lines:,}')
print(f'Syntax Errors: {len(errors)}')
print()
if len(files) == 14 and not errors:
    print('backend/app/planner — COMPLETE ✅')
else:
    if len(files) != 14:
        print(f'MISSING FILES: expected 14, found {len(files)}')
        print('Present:', [f for f,_ in files])
    sys.exit(1)
"