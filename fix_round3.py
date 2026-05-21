import re
import os
import glob

def fix_unclosed_files():
    scripts = glob.glob("scripts/*.py") + glob.glob("backtest.py")
    for script in scripts:
        with open(script, "r") as f:
            content = f.read()
        orig = content
        
        # pickle.load(open(path, mode))
        content = re.sub(
            r'^([ \t]*)([\w_]+)\s*=\s*pickle\.load\(open\((.*?),\s*(.*?)\)\)',
            r'\1with open(\3, \4) as _f:\n\1    \2 = pickle.load(_f)',
            content,
            flags=re.MULTILINE
        )
        
        # pickle.dump(var, open(path, mode))
        content = re.sub(
            r'^([ \t]*)(_p|pickle)\.dump\((.*?),\s*open\((.*?),\s*(.*?)\)\)',
            r'\1with open(\4, \5) as _f:\n\1    \2.dump(\3, _f)',
            content,
            flags=re.MULTILINE
        )
        
        # json.load(open(path))
        content = re.sub(
            r'^([ \t]*)([\w_]+)\s*=\s*json\.load\(open\((.*?)\)\)',
            r'\1with open(\3) as _f:\n\1    \2 = json.load(_f)',
            content,
            flags=re.MULTILINE
        )
        
        # [l for l in open(path) if ...]
        content = re.sub(
            r'^([ \t]*)([\w_]+)\s*=\s*\[(.*?)for\s+([\w_]+)\s+in\s+open\((.*?)\)\s+if(.*?)\]',
            r'\1with open(\5) as _f:\n\1    \2 = [\3for \4 in _f if\6]',
            content,
            flags=re.MULTILINE
        )
        
        # {l for l in open(path) if ...}
        content = re.sub(
            r'^([ \t]*)([\w_]+)\s*=\s*\{(.*?)for\s+([\w_]+)\s+in\s+open\((.*?)\)\s+if(.*?)\}',
            r'\1with open(\5) as _f:\n\1    \2 = {\3for \4 in _f if\6}',
            content,
            flags=re.MULTILINE
        )
        
        # for ln in open(path):
        content = re.sub(
            r'^([ \t]*)for\s+([\w_]+)\s+in\s+open\((.*?)\):',
            r'\1with open(\3) as _f: _lines = _f.readlines()\n\1for \2 in _lines:',
            content,
            flags=re.MULTILINE
        )
        
        if content != orig:
            print(f"Fixed unclosed file in {script}")
            with open(script, "w") as f:
                f.write(content)

def fix_redundant():
    # scripts/paper_trade_today.py
    with open("scripts/paper_trade_today.py", "r") as f:
        c = f.read()
    c = c.replace('if pos >= 1 else None', '')
    c = c.replace('if pos >= 5 else None', '')
    c = c.replace('if pos >= 20 else None', '')
    with open("scripts/paper_trade_today.py", "w") as f: f.write(c)

    # scripts/gen_signals.py
    with open("scripts/gen_signals.py", "r") as f:
        c = f.read()
    c = c.replace('if sg >= 5 else None', '')
    c = c.replace('if sg >= 20 else None', '')
    with open("scripts/gen_signals.py", "w") as f: f.write(c)

    # index.html
    with open("web/static/index.html", "r") as f:
        c = f.read()
    c = c.replace("if (txt.includes('WARNING') || txt.includes('WARNING'))", "if (txt.includes('WARNING'))")
    c = c.replace("let summaryOk = false;\n  if (statusOk) {", "if (statusOk) {")
    c = c.replace("      summaryOk = true;\n    } catch(e) {", "    } catch(e) {")
    with open("web/static/index.html", "w") as f: f.write(c)

if __name__ == "__main__":
    fix_unclosed_files()
    fix_redundant()
